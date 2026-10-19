"""
Configuración no sensible de la aplicación Aules Downloader.
Ajusta aquí URLs, selectores y preferencias cuando cambie la estructura de Aules/Moodle.
"""

# URL base del sabor de Aules (cámbiala según tu etapa educativa)
# Ejemplos verificados:
#   https://aules.edu.gva.es/semipresencial/
#   https://aules.edu.gva.es/fp/
#   https://aules.edu.gva.es/batxillerat/
#   https://aules.edu.gva.es/especials/
#   https://aules.edu.gva.es/primaria/
#   etc. (ver portal.edu.gva.es/aules)
DEFAULT_BASE_URL = "https://aules.edu.gva.es/batxillerat/"
APP_VERSION = "1.1.0"

# Rutas relativas habituales de Moodle
LOGIN_PATH = "login/index.php"
MY_COURSES_PATH = "my/"          # Dashboard / Área personal
COURSE_VIEW_PATH = "course/view.php"

# Selectores CSS/XPath configurables (Moodle 4.x / Aules actual)
# Si dejan de funcionar, inspecciona la página y actualiza solo estos valores.
SELECTORS = {
    # Login
    "username": "#username",
    "password": "#password",
    "login_button": "#loginbtn",
    "login_form": "form#login",
    "login_error": ".loginerrors, .alert-danger, .alert-error",  # mensajes de error comunes

    # Detección de sesión iniciada
    "user_menu": ".usermenu, .userbutton, [data-region='user-menu']",
    "dashboard_indicator": ".dashboard-card, .course-listitem, #page-my-index",

    # Cursos en el área personal / dashboard
    "course_links": "a[href*='/course/view.php?id=']",
    "course_card": ".dashboard-card, .course-listitem, .coursename",

    # Dentro de un curso
    "section": "li.section, .section, [data-region='section']",
    "section_name": ".sectionname, .section-title, h3.sectionname, .section-header h3",
    "activity": ".activity, .activity-item, [data-region='activity-item']",
    "resource_link": "a[href*='/mod/resource/view.php'], a[href*='pluginfile.php']",
    "file_link": "a[href*='pluginfile.php'], a[href*='forcedownload=1']",
}

# Formatos de recursos que se guardan junto a los PDF.
SUPPORTED_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
    ".rtf",
    ".txt",
    ".csv",
}

# Preferencias de descarga
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0          # segundos (exponencial)
REQUEST_TIMEOUT = 60
CHUNK_SIZE = 8192
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Carpeta especial cuando no se identifica sección
NO_SECTION_FOLDER = "Sin sección"

# Longitud máxima de nombres de archivo/carpeta (Windows limita ~260 caracteres de ruta completa)
MAX_FILENAME_LENGTH = 180