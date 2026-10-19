"""
Interfaz gráfica moderna con CustomTkinter.
"""

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from aules_client import AulesClient, SyncStats
from config import APP_VERSION, DEFAULT_BASE_URL


class AulesDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.iconbitmap(str(Path(__file__).with_name("image.ico")))
        self.title(f"Aules PDF Synchronizer v{APP_VERSION}")
        self.geometry("900x700")
        self.minsize(800, 600)

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.client: Optional[AulesClient] = None
        self.is_running = False

        self._build_ui()
        self.after(200, self._process_queue)

    def _build_ui(self):
        # === Panel superior: autenticación ===
        auth_frame = ctk.CTkFrame(self)
        auth_frame.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(auth_frame, text="Autenticación Aules", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4)
        )

        ctk.CTkLabel(auth_frame, text="Usuario (NIA / ITACA / aules):").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self.username_entry = ctk.CTkEntry(auth_frame, width=280)
        self.username_entry.grid(row=1, column=1, sticky="w", padx=5, pady=4)

        ctk.CTkLabel(auth_frame, text="Contraseña:").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self.password_entry = ctk.CTkEntry(auth_frame, width=280, show="•")
        self.password_entry.grid(row=2, column=1, sticky="w", padx=5, pady=4)

        self.show_pass_var = ctk.BooleanVar(value=False)
        self.show_pass_cb = ctk.CTkCheckBox(
            auth_frame, text="Mostrar contraseña", variable=self.show_pass_var, command=self._toggle_password
        )
        self.show_pass_cb.grid(row=2, column=2, sticky="w", padx=10)

        ctk.CTkLabel(auth_frame, text="URL base del sabor:").grid(row=3, column=0, sticky="w", padx=10, pady=4)
        self.base_url_entry = ctk.CTkEntry(auth_frame, width=400)
        self.base_url_entry.insert(0, DEFAULT_BASE_URL)
        self.base_url_entry.grid(row=3, column=1, columnspan=2, sticky="w", padx=5, pady=4)

        self.headless_var = ctk.BooleanVar(value=False)
        self.headless_cb = ctk.CTkCheckBox(
            auth_frame, text="Modo headless (sin ventana de navegador)", variable=self.headless_var
        )
        self.headless_cb.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=6)

        # === Destino ===
        dest_frame = ctk.CTkFrame(self)
        dest_frame.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(dest_frame, text="Carpeta de destino", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4)
        )

        self.dest_entry = ctk.CTkEntry(dest_frame, width=500)
        self.dest_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        dest_frame.columnconfigure(0, weight=1)

        self.browse_btn = ctk.CTkButton(dest_frame, text="Elegir carpeta…", width=120, command=self._browse_folder)
        self.browse_btn.grid(row=1, column=2, padx=10, pady=4)

        self.create_dir_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(dest_frame, text="Crear carpeta si no existe", variable=self.create_dir_var).grid(
            row=2, column=0, sticky="w", padx=10, pady=4
        )

        # === Controles ===
        ctrl_frame = ctk.CTkFrame(self)
        ctrl_frame.pack(fill="x", padx=12, pady=6)

        self.start_btn = ctk.CTkButton(
            ctrl_frame, text="Iniciar sesión y sincronizar", width=220, height=36, command=self._start_sync
        )
        self.start_btn.pack(side="left", padx=10, pady=10)

        self.cancel_btn = ctk.CTkButton(
            ctrl_frame, text="Cancelar", width=120, height=36, fg_color="#c0392b", hover_color="#a93226",
            command=self._cancel_sync, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=5, pady=10)

        self.theme_btn = ctk.CTkButton(
            ctrl_frame, text="Tema claro/oscuro", width=140, command=self._toggle_theme
        )
        self.theme_btn.pack(side="right", padx=10, pady=10)

        # === Consola de actividad ===
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        ctk.CTkLabel(log_frame, text="Consola de actividad", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 2)
        )

        self.log_text = ctk.CTkTextbox(log_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

    def _toggle_password(self):
        self.password_entry.configure(show="" if self.show_pass_var.get() else "•")

    def _toggle_theme(self):
        current = ctk.get_appearance_mode()
        ctk.set_appearance_mode("Dark" if current == "Light" else "Light")

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Seleccionar carpeta de destino")
        if path:
            self.dest_entry.delete(0, tk.END)
            self.dest_entry.insert(0, path)

    def _log(self, message: str):
        self.msg_queue.put(message)

    def _process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self._process_queue)

    def _validate_inputs(self) -> Optional[str]:
        user = self.username_entry.get().strip()
        password = self.password_entry.get()
        dest = self.dest_entry.get().strip()
        base = self.base_url_entry.get().strip()

        if not user:
            return "Introduce el usuario (NIA o usuario ITACA / aules)."
        if not password:
            return "Introduce la contraseña."
        if not dest:
            return "Selecciona una carpeta de destino."
        if not base.startswith("http"):
            return "La URL base debe comenzar por http:// o https://"

        dest_path = Path(dest)
        if not dest_path.exists():
            if self.create_dir_var.get():
                try:
                    dest_path.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    return f"No se pudo crear la carpeta: {e}"
            else:
                return "La carpeta de destino no existe."
        if not os.access(dest_path, os.W_OK):
            return "No tienes permisos de escritura en la carpeta seleccionada."
        return None

    def _start_sync(self):
        error = self._validate_inputs()
        if error:
            messagebox.showerror("Datos incompletos", error)
            return

        if self.is_running:
            return

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        dest = self.dest_entry.get().strip()
        base_url = self.base_url_entry.get().strip()
        headless = self.headless_var.get()

        def worker():
            self.client = AulesClient(
                base_url=base_url,
                headless=headless,
                log_callback=self._log,
            )
            stats = self.client.sync(username, password, dest)
            self.msg_queue.put("__STATS__" + self._format_stats(stats))
            self.msg_queue.put("__DONE__")

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _cancel_sync(self):
        if self.client:
            self.client.request_cancel()
            self._log("Solicitud de cancelación enviada…")

    def _format_stats(self, stats: SyncStats) -> str:
        if stats.error:
            status = "ERROR"
        elif stats.cancelled:
            status = "CANCELADA"
        elif stats.failed or stats.inaccessible:
            status = "CON ERRORES"
        else:
            status = "CORRECTA"
        return (
            f"\n========== RESUMEN ({status}) ==========\n"
            f"Asignaturas analizadas : {stats.courses_analyzed}\n"
            f"Orientación omitida    : {stats.orientation_courses_skipped}\n"
            f"Secciones procesadas   : {stats.sections_processed}\n"
            f"Archivos encontrados   : {stats.files_found}\n"
            f"Descargados            : {stats.downloaded}\n"
            f"Omitidos (ya existían) : {stats.skipped_existing}\n"
            f"Fallidos               : {stats.failed}\n"
            f"Inaccesibles           : {stats.inaccessible}\n"
            f"Carpeta destino        : {stats.destination}\n"
            f"Error                  : {stats.error or '—'}\n"
            f"========================================\n"
        )

    def _on_done(self):
        self.is_running = False
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.client = None

    def _process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                if msg == "__DONE__":
                    self._on_done()
                    continue
                if msg.startswith("__STATS__"):
                    msg = msg[len("__STATS__"):]
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self._process_queue)


# Necesario para la comprobación de permisos
import os