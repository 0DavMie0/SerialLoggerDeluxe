import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import serial.tools.list_ports
import threading
import queue
import time
import datetime
import os
import sys
import configparser
from pathlib import Path
import codecs
from collections import deque
import sv_ttk  # Biblioteca de temas

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def get_current_year():
    return datetime.datetime.now().year


def get_timestamp(format_choice, delimiter):
    if format_choice == "none": return ""
    now = datetime.datetime.now()
    formats = {
        "ISO 8601": now.isoformat(sep='T', timespec='milliseconds'),
        "Date|Time|Timezone": now.astimezone().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' %Z',
        "Date|Time": now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
        "Time": now.strftime('%H:%M:%S.%f')[:-3], "Mod. Julian Date": "No implementado",
        "Year|Day of year|Time": now.strftime('%Y %j %H:%M:%S.%f')[:-3],
        "yyyy|MM|dd|HH|mm|ss": now.strftime('%Y %m %d %H %M %S')
    }
    timestamp = formats.get(format_choice, "")
    if timestamp:
        delimiters = {"blank": " ", "komma": ",", "semicolon": ";", "none": ""}
        return timestamp + delimiters.get(delimiter, " ")
    return ""


class SerialLoggerApp(tk.Tk):
    VERSION = "1.5.3"  # Versión con corrección de logging

    THEME_COLORS = {
        "light": {"bg": "white", "fg": "black", "cursor": "black", "sent_fg": "#00008B", "error_fg": "red"},
        "dark": {"bg": "#2b2b2b", "fg": "#d8d8d8", "cursor": "white", "sent_fg": "#8ab4f8", "error_fg": "#ff7b72"}
    }

    def __init__(self):
        super().__init__()

        self.serial_port = None
        self.serial_reader_thread = None
        self.log_writer_thread = None  # Hilo para el log
        self.is_reading = threading.Event()
        self.gui_queue = queue.Queue()  # Cola para la GUI
        self.log_queue = queue.Queue()  # Cola para el log
        self.append_flag = False
        self.print_log_flag = False
        self.start_of_line = True
        self.command_history = deque(maxlen=50)
        self.history_index = -1

        self.config = configparser.ConfigParser()
        self.config_file = Path("settings.ini")
        self.std_logfile_name = Path.home() / "serial.log"

        self.title("SerialLogger")
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'serial.png')
            self.iconphoto(True, tk.PhotoImage(file=icon_path))
        except tk.TclError:
            print("Advertencia: No se pudo encontrar el archivo de icono 'serial.png'.")

        self._load_and_set_initial_theme()
        self._create_widgets()
        self._load_settings()

        self.after(10, lambda: self._apply_theme(self.current_theme))

        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.update_port_list()

    # --- El resto de las funciones de la GUI y configuración se mantienen ---
    def _set_windows_title_bar_color(self, theme_name):
        if sys.platform != "win32": return
        try:
            hwnd = wintypes.HWND(self.winfo_id())
            value = ctypes.c_int(1 if theme_name == "dark" else 0)
            for attribute in (20, 19):
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                               ctypes.sizeof(value))
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"No se pudo cambiar el color de la barra de título: {e}")

    def _create_widgets(self):
        # ... (código sin cambios)
        main_frame = ttk.Frame(self, padding="10");
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1);
        self.rowconfigure(0, weight=1)
        left_panel = ttk.Frame(main_frame);
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.columnconfigure(0, weight=1);
        left_panel.rowconfigure(2, weight=1)
        main_frame.columnconfigure(0, weight=1);
        main_frame.rowconfigure(0, weight=1)
        port_frame = ttk.Frame(left_panel);
        port_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        port_frame.columnconfigure(1, weight=1)
        ttk.Label(port_frame, text="CommPort:").grid(row=0, column=0, padx=(0, 5))
        self.cb_commport = ttk.Combobox(port_frame, width=20);
        self.cb_commport.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        self.bt_update = ttk.Button(port_frame, text="Update", command=self.update_port_list);
        self.bt_update.grid(row=0, column=2, padx=(0, 5))
        self.bt_clear = ttk.Button(port_frame, text="Clear", command=self._clear_output);
        self.bt_clear.grid(row=0, column=3)
        output_header_frame = ttk.Frame(left_panel);
        output_header_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(output_header_frame, text="Output:").pack(side="left")
        self.hex_view_var = tk.BooleanVar();
        self.ck_hex_view = ttk.Checkbutton(output_header_frame, text="HEX View", variable=self.hex_view_var);
        self.ck_hex_view.pack(side="right")
        self.ta_log_panel = tk.Text(left_panel, wrap='word', state='disabled', height=15, relief="solid", borderwidth=1)
        log_scroll = ttk.Scrollbar(left_panel, orient=tk.VERTICAL, command=self.ta_log_panel.yview)
        self.ta_log_panel.config(yscrollcommand=log_scroll.set)
        self.ta_log_panel.grid(row=2, column=0, sticky="nsew");
        log_scroll.grid(row=2, column=1, sticky="ns")
        ttk.Label(left_panel, text="Input:").grid(row=3, column=0, sticky="w", pady=(5, 2))
        input_frame = ttk.Frame(left_panel);
        input_frame.grid(row=4, column=0, columnspan=2, sticky="ew")
        input_frame.columnconfigure(0, weight=1)
        self.entry_input = ttk.Entry(input_frame, state='disabled');
        self.entry_input.grid(row=0, column=0, sticky="ew")
        self.entry_input.bind("<Return>", self._send_data);
        self.entry_input.bind("<Up>", self._history_up);
        self.entry_input.bind("<Down>", self._history_down)
        self.line_ending_var = tk.StringVar(value="CR+LF (\\r\\n)")
        self.cb_line_ending = ttk.Combobox(input_frame, textvariable=self.line_ending_var,
                                           values=["None", "NL (\\n)", "CR (\\r)", "CR+LF (\\r\\n)"], width=15,
                                           state="readonly");
        self.cb_line_ending.grid(row=0, column=1, padx=(5, 5))
        self.bt_send = ttk.Button(input_frame, text="Send", state='disabled', command=self._send_data);
        self.bt_send.grid(row=0, column=2, sticky="e")
        log_file_frame = ttk.Frame(left_panel);
        log_file_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        log_file_frame.columnconfigure(1, weight=1)
        self.ck_logfile_var = tk.BooleanVar()
        self.ck_logfile = ttk.Checkbutton(log_file_frame, text="Log to:", variable=self.ck_logfile_var);
        self.ck_logfile.grid(row=0, column=0, sticky="w")
        self.tf_logfile = ttk.Entry(log_file_frame);
        self.tf_logfile.grid(row=0, column=1, sticky="ew", padx=5)
        self.bt_fileselector = ttk.Button(log_file_frame, text="...", width=4, command=self._choose_logfile);
        self.bt_fileselector.grid(row=0, column=2, sticky="e")
        right_panel = ttk.Frame(main_frame);
        right_panel.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        top_buttons_frame = ttk.Frame(right_panel);
        top_buttons_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.bt_theme_toggle = ttk.Button(top_buttons_frame, text="Toggle Theme", command=self._toggle_theme);
        self.bt_theme_toggle.pack(side="left", padx=(0, 10))
        self.dtr_var = tk.BooleanVar(value=True)
        self.ck_dtr = ttk.Checkbutton(top_buttons_frame, text="DTR Auto-Reset", variable=self.dtr_var);
        self.ck_dtr.pack(side="left", padx=(0, 10))
        self.bt_info = ttk.Button(top_buttons_frame, text="Info", command=self._show_info);
        self.bt_info.pack(side="left")
        params_frame = ttk.LabelFrame(right_panel, text="Serial Parameters");
        params_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(params_frame, text="Baud:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.cb_baud = ttk.Combobox(params_frame, width=15,
                                    values=["300", "600", "1200", "2400", "4800", "9600", "19200", "38400", "57600",
                                            "115200"]);
        self.cb_baud.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(params_frame, text="Data Bits:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.cb_databits = ttk.Combobox(params_frame, width=15, values=["5", "6", "7", "8"], state="readonly");
        self.cb_databits.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(params_frame, text="Stop Bits:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.cb_stopbits = ttk.Combobox(params_frame, width=15, values=["1", "1.5", "2"], state="readonly");
        self.cb_stopbits.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(params_frame, text="Parity:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.cb_parity = ttk.Combobox(params_frame, width=15, values=["none", "even", "odd", "mark", "space"],
                                      state="readonly");
        self.cb_parity.grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(params_frame, text="Handshake:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.cb_handshake = ttk.Combobox(params_frame, width=15, values=["none", "RTS/CTS", "XON/XOFF"],
                                         state="readonly");
        self.cb_handshake.grid(row=4, column=1, sticky="ew", padx=5, pady=2)
        ts_frame = ttk.LabelFrame(right_panel, text="Timestamp");
        ts_frame.grid(row=2, column=0, sticky="ew", pady=10)
        ttk.Label(ts_frame, text="Format:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.cb_timestamp = ttk.Combobox(ts_frame, width=15,
                                         values=["none", "ISO 8601", "Date|Time|Timezone", "Date|Time", "Time",
                                                 "Mod. Julian Date", "Year|Day of year|Time", "yyyy|MM|dd|HH|mm|ss"],
                                         state="readonly");
        self.cb_timestamp.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.cb_timestamp.bind("<<ComboboxSelected>>", self._toggle_delimiter)
        ttk.Label(ts_frame, text="Delimiter:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.cb_delimiter = ttk.Combobox(ts_frame, width=15, values=["blank", "komma", "semicolon", "none"],
                                         state="readonly");
        self.cb_delimiter.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        self.bt_open_port = ttk.Button(right_panel, text="Open Port", command=self.open_port);
        self.bt_open_port.grid(row=3, column=0, sticky="ew", pady=(10, 5))
        self.bt_close_port = ttk.Button(right_panel, text="Close Port", command=self.close_port, state="disabled");
        self.bt_close_port.grid(row=4, column=0, sticky="ew")

    def _toggle_theme(self):
        new_theme = "dark" if self.current_theme == "light" else "light"
        self._apply_theme(new_theme)

    def _apply_theme(self, theme_name):
        if not hasattr(self, 'ta_log_panel'): return
        self.current_theme = theme_name
        sv_ttk.set_theme(theme_name)
        colors = self.THEME_COLORS[theme_name]
        self.ta_log_panel.config(background=colors["bg"], foreground=colors["fg"], insertbackground=colors["cursor"])
        self.ta_log_panel.tag_config("sent", foreground=colors["sent_fg"])
        self.ta_log_panel.tag_config("received", foreground=colors["fg"])
        self.ta_log_panel.tag_config("error", foreground=colors["error_fg"])
        self._set_windows_title_bar_color(theme_name)

    def _load_and_set_initial_theme(self):
        if not self.config_file.exists():
            self.current_theme = "light"
        else:
            try:
                self.config.read(self.config_file)
                self.current_theme = self.config.get("UI", "theme", fallback="light")
            except configparser.Error:
                self.current_theme = "light"
        sv_ttk.set_theme(self.current_theme)

    def _send_data(self, event=None):
        if 'disabled' in str(self.bt_send.cget('state')): return
        data_to_send = self.entry_input.get()
        if not data_to_send: return
        if not self.command_history or self.command_history[-1] != data_to_send: self.command_history.append(
            data_to_send)
        self.history_index = len(self.command_history)
        line_ending_map = {"None": "", "NL (\\n)": "\n", "CR (\\r)": "\r", "CR+LF (\\r\\n)": "\r\n"}
        line_ending = line_ending_map.get(self.line_ending_var.get(), "")
        try:
            full_data = data_to_send + line_ending
            encoded_data = full_data.encode('utf-8')
            self.serial_port.write(encoded_data)
            self._display_text(None, full_data, "sent")
            # Los datos enviados no se registran en el fichero de log
            self.entry_input.delete(0, tk.END)
        except serial.SerialException as e:
            messagebox.showerror("Error de Envío", f"No se pudieron enviar los datos:\n{e}")

    def _history_up(self, event):
        if self.history_index > 0:
            self.history_index -= 1
            self.entry_input.delete(0, tk.END)
            self.entry_input.insert(0, self.command_history[self.history_index])
        return "break"

    def _history_down(self, event):
        if self.history_index < len(self.command_history):
            if self.history_index < len(self.command_history) - 1:
                self.history_index += 1
                self.entry_input.delete(0, tk.END)
                self.entry_input.insert(0, self.command_history[self.history_index])
            else:
                self.history_index += 1
                self.entry_input.delete(0, tk.END)
        return "break"

    def _toggle_gui_elements(self, enabled):
        # ... (sin cambios)
        target_state = "normal" if enabled else "disabled"
        if not hasattr(self, 'control_widgets') or not hasattr(self, 'cb_commport'):
            self.control_widgets = [
                self.cb_commport, self.bt_update, self.cb_baud, self.cb_databits, self.cb_stopbits,
                self.cb_parity, self.cb_handshake, self.cb_timestamp, self.cb_delimiter,
                self.ck_logfile, self.tf_logfile, self.bt_fileselector, self.bt_open_port, self.ck_dtr
            ]
        for widget in self.control_widgets:
            if isinstance(widget, ttk.Combobox):
                is_readonly = 'readonly' in str(widget.cget('state'))
                if enabled and is_readonly:
                    widget.config(state="readonly")
                else:
                    widget.config(state=target_state)
            else:
                widget.config(state=target_state)

        self.entry_input.config(state="normal" if not enabled else "disabled")
        self.bt_send.config(state="normal" if not enabled else "disabled")
        self.cb_line_ending.config(state="readonly" if not enabled else "disabled")
        self.bt_open_port.config(state="normal" if enabled else "disabled")
        self.bt_close_port.config(state="disabled" if enabled else "normal")
        if enabled:
            self._toggle_delimiter()
        else:
            self.cb_delimiter.config(state="disabled")

    def _toggle_delimiter(self, event=None):
        if not hasattr(self, 'cb_timestamp'): return
        if self.cb_timestamp.get() == "none" or 'disabled' in str(self.cb_timestamp.cget('state')):
            self.cb_delimiter.config(state="disabled")
        else:
            self.cb_delimiter.config(state="readonly")

    def _load_settings(self):
        # ... (sin cambios)
        self._set_defaults()
        if not self.config_file.exists(): return
        is_maximized = self.config.getboolean("Window", "maximized", fallback=False)
        if is_maximized:
            self.state('zoomed')
        else:
            self.geometry(self.config.get("Window", "geometry", fallback="850x650+100+100"))
        self.cb_baud.set(self.config.get("Serial", "baud", fallback=self.cb_baud.get()))
        self.cb_databits.set(self.config.get("Serial", "databits", fallback=self.cb_databits.get()))
        self.cb_stopbits.set(self.config.get("Serial", "stopbits", fallback=self.cb_stopbits.get()))
        self.cb_parity.set(self.config.get("Serial", "parity", fallback=self.cb_parity.get()))
        self.cb_handshake.set(self.config.get("Serial", "handshake", fallback=self.cb_handshake.get()))
        self.dtr_var.set(self.config.getboolean("Serial", "dtr_reset", fallback=True))
        self.cb_timestamp.set(self.config.get("Log", "timestamp", fallback=self.cb_timestamp.get()))
        self.cb_delimiter.set(self.config.get("Log", "delimiter", fallback=self.cb_delimiter.get()))
        self.tf_logfile.delete(0, tk.END);
        self.tf_logfile.insert(0, self.config.get("Log", "file", fallback=self.tf_logfile.get()))
        self.ck_logfile_var.set(self.config.getboolean("Log", "enabled", fallback=self.ck_logfile_var.get()))
        self.line_ending_var.set(self.config.get("UI", "line_ending", fallback="CR+LF (\\r\\n)"))
        self.hex_view_var.set(self.config.getboolean("UI", "hex_view", fallback=False))
        self._toggle_delimiter()

    def _set_defaults(self):
        # ... (sin cambios)
        self.cb_baud.set("9600");
        self.cb_databits.set("8");
        self.cb_stopbits.set("1");
        self.cb_parity.set("none");
        self.cb_handshake.set("none");
        self.cb_timestamp.set("none");
        self.cb_delimiter.set("blank");
        self.tf_logfile.insert(0, str(self.std_logfile_name));
        self.ck_logfile_var.set(False);
        self._toggle_delimiter();

    def _save_settings(self):
        # ... (sin cambios)
        if 'Window' not in self.config: self.config.add_section('Window')
        is_maximized = self.state() == 'zoomed'
        self.config.set('Window', 'maximized', str(is_maximized))
        if not is_maximized: self.config.set('Window', 'geometry', self.geometry())
        if 'Serial' not in self.config: self.config.add_section('Serial')
        self.config.set('Serial', 'baud', self.cb_baud.get());
        self.config.set('Serial', 'databits', self.cb_databits.get());
        self.config.set('Serial', 'stopbits', self.cb_stopbits.get());
        self.config.set('Serial', 'parity', self.cb_parity.get());
        self.config.set('Serial', 'handshake', self.cb_handshake.get());
        self.config.set('Serial', 'dtr_reset', str(self.dtr_var.get()))
        if 'Log' not in self.config: self.config.add_section('Log')
        self.config.set('Log', 'timestamp', self.cb_timestamp.get());
        self.config.set('Log', 'delimiter', self.cb_delimiter.get());
        self.config.set('Log', 'file', self.tf_logfile.get());
        self.config.set('Log', 'enabled', str(self.ck_logfile_var.get()));
        if 'UI' not in self.config: self.config.add_section('UI')
        self.config.set('UI', 'line_ending', self.line_ending_var.get())
        self.config.set('UI', 'hex_view', str(self.hex_view_var.get()))
        self.config.set('UI', 'theme', self.current_theme)
        with open(self.config_file, 'w') as configfile:
            self.config.write(configfile)

    def _on_closing(self):
        # ... (sin cambios)
        buffer_content = self.ta_log_panel.get("1.0", "end-1c")
        if buffer_content and not self.print_log_flag:
            response = messagebox.askyesnocancel("Buffer no guardado",
                                                 "El buffer contiene datos sin guardar.\n¿Desea guardarlos antes de salir?")
            if response is True:
                if not self._save_buffer(): return
            elif response is None:
                return
        self._save_settings();
        if self.is_reading.is_set(): self.close_port();
        self.destroy();

    def update_port_list(self):
        # ... (sin cambios)
        ports = serial.tools.list_ports.comports()
        port_names = [port.device for port in ports]
        self.cb_commport['values'] = port_names
        if port_names:
            self.cb_commport.set(port_names[0])
        else:
            self.cb_commport.set("")

    def open_port(self):
        self._clear_output()
        log_to_file = self.ck_logfile_var.get()
        log_filename = self.tf_logfile.get()

        if log_to_file:
            if not log_filename: messagebox.showerror("Error", "El nombre del archivo de log está vacío."); return
            if os.path.exists(log_filename):
                if not messagebox.askyesno("Archivo existente",
                                           f"El archivo '{log_filename}' ya existe. ¿Añadir datos?"): return
                self.append_flag = True
            else:
                self.append_flag = False
            self.print_log_flag = True
        else:
            self.print_log_flag = False

        port_name = self.cb_commport.get()
        if not port_name: messagebox.showerror("Error", "No se ha seleccionado un puerto COM."); return
        try:
            self.serial_port = serial.Serial()
            self.serial_port.port = port_name;
            self.serial_port.baudrate = int(self.cb_baud.get());
            self.serial_port.bytesize = \
            {'5': serial.FIVEBITS, '6': serial.SIXBITS, '7': serial.SEVENBITS, '8': serial.EIGHTBITS}[
                self.cb_databits.get()]
            self.serial_port.stopbits = \
            {'1': serial.STOPBITS_ONE, '1.5': serial.STOPBITS_ONE_POINT_FIVE, '2': serial.STOPBITS_TWO}[
                self.cb_stopbits.get()]
            self.serial_port.parity = {'none': serial.PARITY_NONE, 'even': serial.PARITY_EVEN, 'odd': serial.PARITY_ODD,
                                       'mark': serial.PARITY_MARK, 'space': serial.PARITY_SPACE}[self.cb_parity.get()]
            handshake = self.cb_handshake.get();
            self.serial_port.rtscts = (handshake == "RTS/CTS");
            self.serial_port.xonxoff = (handshake == "XON/XOFF")
            self.serial_port.dtr = self.dtr_var.get()
            self.serial_port.timeout = 0.02
            self.serial_port.open()
        except (serial.SerialException, ValueError, KeyError) as e:
            messagebox.showerror("Error de Puerto Serie", f"No se pudo abrir el puerto {port_name}:\n{e}");
            self.serial_port = None;
            return

        self.start_of_line = True
        self._toggle_gui_elements(False)
        self.is_reading.set()

        self.serial_reader_thread = threading.Thread(target=self._serial_read_task, daemon=True)
        self.serial_reader_thread.start()

        if log_to_file:
            self.log_writer_thread = threading.Thread(target=self._log_writer_task, daemon=True)
            self.log_writer_thread.start()

        self.after(50, self._process_gui_queue)

    def close_port(self):
        if self.serial_port and self.serial_port.is_open:
            self.is_reading.clear()
            self.after(200, self._finalize_close)

    def _finalize_close(self):
        if self.serial_port: self.serial_port.close(); self.serial_port = None
        # Enviar señal de finalización a la cola del log
        if self.log_writer_thread and self.log_writer_thread.is_alive():
            self.log_queue.put(None)
        self._toggle_gui_elements(True);
        print("Puerto cerrado.")

    def _serial_read_task(self):
        print("Hilo de lectura iniciado.")
        log_enabled = self.ck_logfile_var.get()
        try:
            while self.is_reading.is_set():
                try:
                    raw_chunk = self.serial_port.read(128)
                    if raw_chunk:
                        self.gui_queue.put(raw_chunk)
                        if log_enabled:
                            self.log_queue.put(raw_chunk)
                except serial.SerialException:
                    self.gui_queue.put(b"\n--- ERROR: Puerto serie desconectado ---\n")
                    if log_enabled: self.log_queue.put(None)  # Terminar el hilo de log
                    self.is_reading.clear();
                    break
        finally:
            if log_enabled: self.log_queue.put(None)  # Asegurarse de que el hilo de log termine
            print("Hilo de lectura terminado.")

    def _log_writer_task(self):
        print("Hilo de log iniciado.")
        log_filename = self.tf_logfile.get()
        mode = 'a' if self.append_flag else 'w'
        line_buffer = ''
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

        try:
            with open(log_filename, mode, encoding='utf-8') as logfile:
                while True:
                    raw_chunk = self.log_queue.get()
                    if raw_chunk is None:  # Señal de finalización
                        break

                    decoded_string = decoder.decode(raw_chunk)
                    line_buffer += decoded_string
                    while '\n' in line_buffer:
                        line, line_buffer = line_buffer.split('\n', 1)
                        timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                        logfile.write(f"{timestamp}{line}\n")
                        logfile.flush()
                # Escribir lo que quede en el buffer al final
                if line_buffer:
                    timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                    logfile.write(f"{timestamp}{line_buffer.strip()}\n")
        except IOError as e:
            print(f"Error al escribir en el log: {e}")
        finally:
            print("Hilo de log terminado.")

    def _process_gui_queue(self):
        try:
            while not self.gui_queue.empty():
                raw_chunk = self.gui_queue.get_nowait()
                self._display_text(raw_chunk, None, "received")
        except queue.Empty:
            pass
        if self.is_reading.is_set(): self.after(50, self._process_gui_queue)

    def _display_text(self, raw_data, text_data, tag):
        self.ta_log_panel.config(state='normal')

        is_hex = self.hex_view_var.get()
        display_string = ""

        if raw_data:  # Datos recibidos (bytes)
            if is_hex:
                display_string = ' '.join(f'{b:02X}' for b in raw_data) + ' '
                if b'\n' in raw_data: display_string += '\n'
            else:
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                display_string = decoder.decode(raw_data)
        elif text_data:  # Datos enviados (texto)
            display_string = text_data
            if is_hex:
                display_string = ' '.join(f'{ord(c):02X}' for c in text_data if ord(c) <= 255) + ' '
                if '\n' in text_data: display_string += '\n'
        for char in display_string:
            if self.start_of_line:
                timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                if timestamp: self.ta_log_panel.insert(tk.END, timestamp, "received")
                self.start_of_line = False
            self.ta_log_panel.insert(tk.END, char, tag)
            if char == '\n':
                self.start_of_line = True

        self.ta_log_panel.see(tk.END)
        self.ta_log_panel.config(state='disabled')

    def _clear_output(self):
        self.ta_log_panel.config(state='normal')
        self.ta_log_panel.delete('1.0', tk.END)
        self.ta_log_panel.config(state='disabled')
        self.start_of_line = True

    def _choose_logfile(self):
        filename = filedialog.asksaveasfilename(title="Especificar archivo de log", initialdir=Path.home(),
                                                initialfile="serial.log", defaultextension=".log",
                                                filetypes=[("Log files", "*.log"), ("Text files", "*.txt"),
                                                           ("All files", "*.*")])
        if filename: self.tf_logfile.delete(0, tk.END); self.tf_logfile.insert(0, filename)

    def _save_buffer(self):
        filename = filedialog.asksaveasfilename(title="Guardar buffer", defaultextension=".txt",
                                                filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not filename: return False
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.ta_log_panel.get("1.0", "end-1c"))
            messagebox.showinfo("Guardado", f"Buffer guardado en {filename}");
            self.print_log_flag = True;
            return True
        except IOError as e:
            messagebox.showerror("Error al guardar", f"No se pudo guardar el archivo:\n{e}"); return False

    def _show_info(self):
        messagebox.showinfo("Info",
                            f"SerialLogger v{self.VERSION}\n\nRegistra y permite interactuar con dispositivos serie.\n\n(c) 2013-2021 Hani Ibrahim\n(c) 2025 Convertido a Python por David Talavera y Google AI Studio\nTraducción y mejoras por AI\n\nLicencia: GNU Public License 3.0")


if __name__ == "__main__":
    app = SerialLoggerApp()
    app.mainloop()
