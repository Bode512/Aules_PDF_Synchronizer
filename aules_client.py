"""
Cliente de Aules: autenticación, descubrimiento de cursos y recursos PDF.
Todo lo específico de la estructura HTML está parametrizado vía config.SELECTORS.
"""

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Dict
from urllib.parse import unquote, urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
)
from webdriver_manager.chrome import ChromeDriverManager

from config import (
    DEFAULT_BASE_URL,
    LOGIN_PATH,
    MY_COURSES_PATH,
    SELECTORS,
    SUPPORTED_FILE_EXTENSIONS,
)
from downloader import AuthenticatedDownloader, DownloadResult
from file_manager import build_destination_path, sanitize_filename

logger = logging.getLogger(__name__)


@dataclass
class ResourceInfo:
    name: str
    url: str
    section: Optional[str] = None
    course_name: str = ""


@dataclass
class CourseInfo:
    name: str
    url: str
    resources: List[ResourceInfo] = field(default_factory=list)


@dataclass
class SyncStats:
    courses_analyzed: int = 0
    orientation_courses_skipped: int = 0
    sections_processed: int = 0
    files_found: int = 0
    downloaded: int = 0
    skipped_existing: int = 0
    failed: int = 0
    inaccessible: int = 0
    cancelled: bool = False
    destination: str = ""
    error: Optional[str] = None


class AulesClient:
    """
    Gestiona el navegador Selenium y la extracción de datos de Aules.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        headless: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.headless = headless
        self.log = log_callback or (lambda msg: logger.info(msg))
        self.driver: Optional[webdriver.Chrome] = None
        self.downloader: Optional[AuthenticatedDownloader] = None
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def _emit(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log(f"[{timestamp}] {message}")

    def start_browser(self):
        self._emit("Preparando navegador...")
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,900")

        self._emit("Comprobando ChromeDriver...")
        service = Service(ChromeDriverManager().install())
        self._emit("Iniciando Chrome...")
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(60)
        self._emit("Navegador listo.")

    def stop_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._emit("Navegador cerrado.")

    def login(self, username: str, password: str, wait_manual_seconds: int = 120) -> bool:
        """
        Realiza el login. Si se detecta necesidad de intervención manual
        (MFA, CAPTCHA, redirección externa), espera en modo visible.
        """
        if not self.driver:
            raise RuntimeError("Navegador no iniciado")

        login_url = urljoin(self.base_url, LOGIN_PATH)
        self._emit(f"Iniciando sesión en Aules... ({login_url})")
        self.driver.get(login_url)

        try:
            # Esperar formulario de login
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["username"]))
            )
        except TimeoutException:
            self._emit("No se encontró el formulario de login. Comprueba la URL base.")
            return False

        # Rellenar credenciales
        user_el = self.driver.find_element(By.CSS_SELECTOR, SELECTORS["username"])
        pass_el = self.driver.find_element(By.CSS_SELECTOR, SELECTORS["password"])
        user_el.clear()
        user_el.send_keys(username)
        pass_el.clear()
        pass_el.send_keys(password)

        # Enviar
        btn = self.driver.find_element(By.CSS_SELECTOR, SELECTORS["login_button"])
        btn.click()
        self._emit("Credenciales enviadas. Esperando respuesta...")

        # Esperar a que desaparezca el formulario o aparezca el menú de usuario
        try:
            WebDriverWait(self.driver, 25).until(
                lambda d: self._is_logged_in() or self._has_login_error()
            )
        except TimeoutException:
            pass

        if self._has_login_error():
            self._emit("Error de autenticación: usuario o contraseña incorrectos (o bloqueo temporal).")
            return False

        if self._is_logged_in():
            self._emit("Autenticación completada.")
            self._init_downloader()
            return True

        # Posible redirección a identidad GVA / Cl@ve / MFA
        self._emit(
            "No se detectó automáticamente el área personal. "
            "Si aparece una verificación adicional (Cl@ve, certificado, CAPTCHA...), "
            "complétala manualmente en el navegador."
        )
        if self.headless:
            self._emit("Estás en modo headless: cambia a navegador visible y vuelve a intentar.")
            return False

        # Esperar intervención manual
        deadline = time.time() + wait_manual_seconds
        while time.time() < deadline:
            if self.is_cancelled():
                return False
            if self._is_logged_in():
                self._emit("Autenticación completada (intervención manual).")
                self._init_downloader()
                return True
            time.sleep(1.5)

        self._emit("Tiempo de espera agotado para autenticación manual.")
        return False

    def _is_logged_in(self) -> bool:
        try:
            self.driver.find_element(By.CSS_SELECTOR, SELECTORS["user_menu"])
            return True
        except NoSuchElementException:
            pass
        try:
            self.driver.find_element(By.CSS_SELECTOR, SELECTORS["dashboard_indicator"])
            return True
        except NoSuchElementException:
            return False

    def _has_login_error(self) -> bool:
        try:
            err = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["login_error"])
            return len(err) > 0
        except Exception:
            return False

    def _init_downloader(self):
        cookies = self.driver.get_cookies()
        self.downloader = AuthenticatedDownloader(cookies, self.base_url)

    @staticmethod
    def _is_orientation_course(course_name: str) -> bool:
        normalized = unicodedata.normalize("NFKD", course_name)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.search(r"\borientacion\b", normalized.casefold()) is not None

    def get_courses(self) -> List[CourseInfo]:
        """Extrae la lista de cursos visibles en el área personal."""
        self._emit("Buscando asignaturas disponibles...")
        my_url = urljoin(self.base_url, MY_COURSES_PATH)
        self.driver.get(my_url)
        time.sleep(1.5)  # pequeña espera para JS de Moodle

        courses: Dict[str, CourseInfo] = {}
        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["course_links"])
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    name = (link.text or "").strip()
                    if not href or "/course/view.php" not in href:
                        continue
                    # Filtrar enlaces de edición o basura
                    if "edit" in href or "role=" in href:
                        continue
                    if not name:
                        # Intentar obtener el nombre del padre o atributo title
                        name = link.get_attribute("title") or "Curso sin nombre"
                    name = sanitize_filename(name)
                    if href not in courses:
                        courses[href] = CourseInfo(name=name, url=href)
                except Exception:
                    continue
        except Exception as e:
            self._emit(f"Error al listar cursos: {e}")

        result = list(courses.values())
        self._emit(f"Se encontraron {len(result)} asignaturas.")
        return result

    def extract_resources_from_course(self, course: CourseInfo) -> List[ResourceInfo]:
        """Navega al curso y extrae enlaces a documentos compatibles."""
        if self.is_cancelled():
            return []

        self._emit(f"Analizando asignatura: {course.name}")
        try:
            self.driver.get(course.url)
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
            time.sleep(1.0)
        except Exception as e:
            self._emit(f"No se pudo cargar el curso {course.name}: {e}")
            return []

        resources: List[ResourceInfo] = []
        seen_urls = set()

        # Intentar recorrer secciones
        sections = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["section"])
        if not sections:
            # Fallback: buscar todos los enlaces de recurso en la página
            sections = [self.driver.find_element(By.TAG_NAME, "body")]

        for section_el in sections:
            if self.is_cancelled():
                break
            section_name = None
            try:
                name_el = section_el.find_element(By.CSS_SELECTOR, SELECTORS["section_name"])
                section_name = name_el.text.strip() or None
            except NoSuchElementException:
                pass

            if section_name:
                self._emit(f"  Procesando sección: {section_name}")

            # Buscar enlaces de recurso dentro de la sección
            try:
                links = section_el.find_elements(By.CSS_SELECTOR, SELECTORS["resource_link"])
            except Exception:
                links = []

            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    text = (link.text or "").strip()
                    if not href or href in seen_urls:
                        continue
                    url_extension = Path(unquote(urlparse(href).path)).suffix.lower()
                    text_extension = Path(text).suffix.lower()
                    is_moodle_resource = "/mod/resource/view.php" in href
                    if not (
                        url_extension in SUPPORTED_FILE_EXTENSIONS
                        or text_extension in SUPPORTED_FILE_EXTENSIONS
                        or is_moodle_resource
                    ):
                        continue

                    name = text or self._guess_filename_from_url(href) or "recurso"

                    seen_urls.add(href)
                    resources.append(
                        ResourceInfo(
                            name=name,
                            url=href,
                            section=section_name,
                            course_name=course.name,
                        )
                    )
                except Exception:
                    continue

        self._emit(f"  Archivos compatibles en {course.name}: {len(resources)}")
        return resources

    def _guess_filename_from_url(self, url: str) -> Optional[str]:
        try:
            path = unquote(urlparse(url).path)
            name = path.split("/")[-1]
            if Path(name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
                return name
            if "pluginfile.php" in path:
                parts = path.split("/")
                for p in reversed(parts):
                    if Path(p).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
                        return p
        except Exception:
            pass
        return None

    def sync(
        self,
        username: str,
        password: str,
        dest_dir: str,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> SyncStats:
        """
        Flujo completo de sincronización.
        """
        stats = SyncStats(destination=dest_dir)
        from pathlib import Path
        base = Path(dest_dir)

        try:
            self.start_browser()
            if not self.login(username, password):
                stats.error = "No se pudo completar la autenticación."
                self._emit(stats.error)
                return stats

            courses = self.get_courses()
            orientation_courses = [
                course for course in courses if self._is_orientation_course(course.name)
            ]
            stats.orientation_courses_skipped = len(orientation_courses)
            if orientation_courses:
                self._emit(
                    f"Se omiten {len(orientation_courses)} asignatura(s) de Orientación, "
                    "según la configuración solicitada."
                )
            courses = [
                course for course in courses if not self._is_orientation_course(course.name)
            ]
            stats.courses_analyzed = len(courses)

            for course in courses:
                if self.is_cancelled():
                    stats.cancelled = True
                    self._emit("Sincronización cancelada.")
                    break

                resources = self.extract_resources_from_course(course)
                stats.files_found += len(resources)

                # Contar secciones aproximadas
                sections = {r.section for r in resources if r.section}
                stats.sections_processed += len(sections) or (1 if resources else 0)

                for res in resources:
                    if self.is_cancelled():
                        stats.cancelled = True
                        break

                    self._emit(f"Descargando: {res.name}")
                    final_path = build_destination_path(
                        base, res.course_name, res.section, res.name
                    )

                    result: DownloadResult = self.downloader.download(
                        res.url,
                        final_path,
                        cancel_check=self.is_cancelled,
                        on_progress=lambda m: self._emit(m),
                    )

                    if result.skipped:
                        self._emit(f"Archivo existente, se omite: {res.name}")
                        stats.skipped_existing += 1
                    elif result.success:
                        self._emit(f"Descarga completada: {res.name}")
                        stats.downloaded += 1
                    else:
                        self._emit(f"Error al descargar {res.name}: {result.error}")
                        if "denegado" in (result.error or "").lower() or "caducada" in (result.error or "").lower():
                            stats.inaccessible += 1
                        else:
                            stats.failed += 1

            if not stats.cancelled:
                self._emit("Proceso finalizado.")

        except Exception as e:
            stats.error = str(e)
            self._emit(f"Error crítico: {e}")
            logger.exception("Error en sync")
        finally:
            self.stop_browser()

        return stats
