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
import json
import argparse
from typing import Deque, Dict, Any, Tuple, Optional, Callable, Literal, Union

# --- Dependencias opcionales con manejo de errores ---
try:
    import sv_ttk

    SV_TTK_AVAILABLE = True
except ImportError:
    SV_TTK_AVAILABLE = False

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes


# --- Constantes para claves de configuración ---
class Cfg:
    # [Window]
    SEC_WINDOW = "Window"
    KEY_GEOMETRY = "geometry"
    KEY_MAXIMIZED = "maximized"
    KEY_TOPMOST = "topmost"
    # [Serial]
    SEC_SERIAL = "Serial"
    KEY_BAUD = "baud"
    KEY_DATABITS = "databits"
    KEY_STOPBITS = "stopbits"
    KEY_PARITY = "parity"
    KEY_HANDSHAKE = "handshake"
    KEY_DTR_RESET = "dtr_reset"
    KEY_ENCODING = "encoding"
    # [Log]
    SEC_LOG = "Log"
    KEY_TIMESTAMP = "timestamp"
    KEY_DELIMITER = "delimiter"
    KEY_FILE = "file"
    KEY_ENABLED = "enabled"
    # [UI]
    SEC_UI = "UI"
    KEY_THEME = "theme"
    KEY_LINE_ENDING = "line_ending"
    KEY_HEX_VIEW = "hex_view"
    KEY_HEX_INPUT = "hex_input"
    KEY_PROTOCOL = "protocol"
    KEY_AUTOSCROLL = "autoscroll"
    KEY_SHOW_CTRL_CHARS = "show_control_chars"  # <-- Nueva clave


# --- Funciones de ayuda y decodificadores (Sin cambios) ---
def get_current_year() -> int:
    return datetime.datetime.now().year


def get_timestamp(format_choice: str, delimiter: str) -> str:
    if format_choice == "none": return ""
    now = datetime.datetime.now()
    formats = {"ISO 8601": now.isoformat(sep='T', timespec='milliseconds'),
               "Date|Time|Timezone": now.astimezone().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' %Z',
               "Date|Time": now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], "Time": now.strftime('%H:%M:%S.%f')[:-3],
               "Mod. Julian Date": "No implementado", "Year|Day of year|Time": now.strftime('%Y %j %H:%M:%S.%f')[:-3],
               "yyyy|MM|dd|HH|mm|ss": now.strftime('%Y %m %d %H %M %S')}
    timestamp = formats.get(format_choice, "")
    if timestamp:
        delimiters = {"blank": " ", "komma": ",", "semicolon": ";", "none": ""}
        return timestamp + delimiters.get(delimiter, " ")
    return ""


def parse_nmea_sentence(line: str) -> str:
    line = line.strip()
    if not line.startswith('$') or '*' not in line: return f"[NO NMEA] {line}\n"
    parts = line.split('*');
    if len(parts) != 2: return f"[FORMATO NMEA INVÁLIDO] {line}\n"
    sentence, checksum_str = parts;
    sentence_data = sentence[1:]
    calculated_checksum = 0
    for char in sentence_data: calculated_checksum ^= ord(char)
    try:
        received_checksum = int(checksum_str, 16)
    except ValueError:
        return f"[CHECKSUM INVÁLIDO] {line}\n"
    if calculated_checksum != received_checksum: return f"[ERROR CHECKSUM: Calc={calculated_checksum:02X}, Recib={received_checksum:02X}] {line}\n"
    fields = sentence_data.split(',');
    msg_type = fields[0]
    if msg_type == "GPGGA":
        try:
            return (f"--- GGA: Global Positioning System Fix Data ---\n"
                    f"  Hora (UTC):      {fields[1][:2]}:{fields[1][2:4]}:{fields[1][4:]}\n"
                    f"  Latitud:         {fields[2]} {fields[3]}\n"
                    f"  Longitud:        {fields[4]} {fields[5]}\n"
                    f"  Calidad Fix:     {fields[6]} (0=inv, 1=GPS, 2=DGPS)\n"
                    f"  Satélites:       {fields[7]}\n"
                    f"  HDOP:            {fields[8]}\n"
                    f"  Altitud:         {fields[9]} {fields[10]}\n"
                    f"--------------------------------------------------\n")
        except IndexError:
            return f"[GPGGA MAL FORMADO] {line}\n"
    elif msg_type == "GPRMC":
        try:
            return (f"--- RMC: Recommended Minimum Specific GNSS Data ---\n"
                    f"  Hora (UTC):      {fields[1][:2]}:{fields[1][2:4]}:{fields[1][4:]}\n"
                    f"  Estado:          {'A=Activo, V=Vacio' if fields[2] in 'AV' else fields[2]}\n"
                    f"  Velocidad (nudos): {fields[7]}\n"
                    f"  Rumbo:           {fields[8]}\n"
                    f"  Fecha:           {fields[9][:2]}/{fields[9][2:4]}/20{fields[9][4:]}\n"
                    f"--------------------------------------------------\n")
        except IndexError:
            return f"[GPRMC MAL FORMADO] {line}\n"
    else:
        return f"[TIPO NMEA NO SOPORTADO: {msg_type}] {line}\n"


def calculate_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1; crc ^= 0xA001
            else:
                crc >>= 1
    return crc


def parse_modbus_rtu(raw_bytes: bytes) -> str:
    if len(raw_bytes) < 4: return f"[TRAMA MODBUS MUY CORTA: {raw_bytes.hex(' ').upper()}]\n"
    address = raw_bytes[0];
    function_code = raw_bytes[1];
    payload = raw_bytes[2:-2]
    received_crc = int.from_bytes(raw_bytes[-2:], 'little')
    calculated_crc = calculate_crc16(raw_bytes[:-2])
    crc_status = "OK" if received_crc == calculated_crc else f"ERROR (Calc: {calculated_crc:04X})"
    func_map = {1: "Read Coils", 2: "Read Discrete Inputs", 3: "Read Holding Registers", 4: "Read Input Registers",
                5: "Write Single Coil", 6: "Write Single Register"}
    func_name = func_map.get(function_code, f"Desconocido (0x{function_code:02X})")
    return (f"--- MODBUS RTU ---\n"
            f"  ID Esclavo:      {address}\n"
            f"  Función:         {function_code} ({func_name})\n"
            f"  Datos (Hex):     {payload.hex(' ').upper()}\n"
            f"  CRC Recibido:    {received_crc:04X} ({crc_status})\n"
            f"---------------------\n")


def parse_can_ascii(line: str) -> str:
    line = line.strip()
    if not line or line[0].lower() != 't': return f"[NO CAN-ASCII] {line}\n"
    try:
        can_id_str = line[1:4];
        data_len = int(line[4]);
        data_str = line[5:5 + data_len * 2]
        can_id = int(can_id_str, 16);
        data_bytes = bytes.fromhex(data_str)
        return (f"--- CAN ASCII ---\n"
                f"  ID:            0x{can_id:03X}\n"
                f"  DLC:           {data_len}\n"
                f"  Datos:         {' '.join(f'{b:02X}' for b in data_bytes)}\n"
                f"-------------------\n")
    except (ValueError, IndexError):
        return f"[CAN-ASCII MAL FORMADO] {line}\n"


def parse_json_line(line: str) -> str:
    line = line.strip()
    if not line.startswith('{') or not line.endswith('}'): return f"[NO JSON] {line}\n"
    try:
        data = json.loads(line)
        pretty_json = json.dumps(data, indent=2)
        return f"--- JSON Object ---\n{pretty_json}\n-------------------\n"
    except json.JSONDecodeError:
        return f"[JSON MAL FORMADO] {line}\n"


class SerialHandler:
    def __init__(self, output_callback: Optional[Callable[[bytes], None]] = None):
        self.port_config: Dict[str, Any] = {}
        self.serial_port: Optional[serial.Serial] = None
        self.is_reading = threading.Event()
        self.reader_thread: Optional[threading.Thread] = None
        self.output_callback = output_callback

    def open_port(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        self.port_config = config
        try:
            self.serial_port = serial.Serial()
            self.serial_port.port = config.get('port')
            self.serial_port.baudrate = int(config.get('baud', 9600))
            self.serial_port.bytesize = \
            {'5': serial.FIVEBITS, '6': serial.SIXBITS, '7': serial.SEVENBITS, '8': serial.EIGHTBITS}[
                str(config.get('databits', 8))]
            self.serial_port.stopbits = \
            {'1': serial.STOPBITS_ONE, '1.5': serial.STOPBITS_ONE_POINT_FIVE, '2': serial.STOPBITS_TWO}[
                str(config.get('stopbits', 1))]
            self.serial_port.parity = {'none': serial.PARITY_NONE, 'even': serial.PARITY_EVEN, 'odd': serial.PARITY_ODD,
                                       'mark': serial.PARITY_MARK, 'space': serial.PARITY_SPACE}[
                config.get('parity', 'none')]
            handshake = config.get('handshake', 'none')
            self.serial_port.rtscts = handshake == "RTS/CTS"
            self.serial_port.xonxoff = handshake == "XON/XOFF"
            self.serial_port.dtr = config.get('dtr', True)
            self.serial_port.timeout = 0.05
            self.serial_port.open()
            self.is_reading.set()
            self.reader_thread = threading.Thread(target=self._read_task, daemon=True)
            self.reader_thread.start()
            return True, f"Puerto {self.port_config['port']} abierto."
        except (serial.SerialException, ValueError, KeyError) as e:
            return False, f"Error al abrir puerto {config.get('port')}:\n{e}"

    def close_port(self) -> None:
        if self.serial_port and self.serial_port.is_open:
            self.is_reading.clear()
            if self.reader_thread and self.reader_thread.is_alive(): self.reader_thread.join(timeout=1)
            self.serial_port.close()
            self.serial_port = None
            print("Puerto cerrado.")

    def _read_task(self) -> None:
        print("Hilo de lectura iniciado.")
        try:
            while self.is_reading.is_set():
                try:
                    if self.serial_port and self.serial_port.in_waiting > 0:
                        if self.port_config.get('protocol') == "Modbus-RTU":
                            time.sleep(0.02)
                            data = self.serial_port.read(self.serial_port.in_waiting)
                        else:
                            data = self.serial_port.read(128)

                        if data and self.output_callback:
                            self.output_callback(data)
                except serial.SerialException:
                    if self.output_callback: self.output_callback(b"\n--- ERROR: Puerto serie desconectado ---\n")
                    self.is_reading.clear()
                    break
                time.sleep(0.01)
        finally:
            print("Hilo de lectura terminado.")

    def write_data(self, data: Union[str, bytes], encoding: str = 'utf-8') -> Tuple[bool, Optional[str]]:
        if self.serial_port and self.serial_port.is_open:
            try:
                if isinstance(data, str):
                    self.serial_port.write(data.encode(encoding))
                elif isinstance(data, bytes):
                    self.serial_port.write(data)
                return True, None
            except serial.SerialException as e:
                return False, f"Error al enviar datos: {e}"
        return False, "Puerto no está abierto."


def run_cli_mode(args):
    print(f"--- SerialLogger CLI v{SerialLoggerApp.VERSION} ---");
    print("Presiona Ctrl+C para salir.")
    config = {'port': args.port, 'baud': args.baud, 'databits': args.databits, 'stopbits': args.stopbits,
              'parity': args.parity, 'dtr': not args.no_dtr, 'timestamp': args.timestamp, 'delimiter': ' ',
              'encoding': args.encoding}
    logfile = None;
    line_buffer = '';
    start_of_line = True
    try:
        decoder = codecs.getincrementaldecoder(config['encoding'])(errors='replace')
    except Exception as e:
        print(f"Error: Codificación '{config['encoding']}' no válida. {e}"); return
    if args.log:
        try:
            logfile = open(args.log, 'a', encoding='utf-8'); print(f"Registrando en: {args.log}")
        except IOError as e:
            print(f"Error al abrir el archivo de log: {e}"); logfile = None

    def console_output(raw_chunk):
        nonlocal start_of_line, line_buffer
        decoded_string = decoder.decode(raw_chunk)
        for char in decoded_string:
            if start_of_line:
                ts = get_timestamp(config['timestamp'], config['delimiter'])
                if ts: sys.stdout.write(ts)
                start_of_line = False
            sys.stdout.write(char);
            sys.stdout.flush()
            if char == '\n': start_of_line = True
        if logfile:
            line_buffer += decoded_string
            while '\n' in line_buffer:
                line, line_buffer = line_buffer.split('\n', 1)
                ts = get_timestamp(config['timestamp'], config['delimiter'])
                logfile.write(f"{ts}{line}\n");
                logfile.flush()

    handler = SerialHandler(output_callback=console_output)
    success, message = handler.open_port(config)
    print(message)
    if not success: return
    try:
        while handler.is_reading.is_set(): time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        handler.close_port()
        if logfile:
            if line_buffer:
                ts = get_timestamp(config['timestamp'], config['delimiter'])
                logfile.write(f"{ts}{line_buffer.strip()}\n")
            logfile.close();
            print("Archivo de log cerrado.")


class SearchDialog(tk.Toplevel):
    def __init__(self, parent, text_widget):
        super().__init__(parent)
        self.transient(parent)
        self.title("Buscar")
        self.text_widget = text_widget
        self.last_search = ""

        self.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))

        self.search_var = tk.StringVar()
        self.case_var = tk.BooleanVar()

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Buscar:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.entry = ttk.Entry(frame, textvariable=self.search_var, width=30)
        self.entry.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="we")
        self.entry.focus_set()

        self.case_check = ttk.Checkbutton(frame, text="Sensible a mayúsculas", variable=self.case_var)
        self.case_check.grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky="w")

        self.find_button = ttk.Button(frame, text="Buscar Siguiente", command=self.find_next)
        self.find_button.grid(row=2, column=1, padx=5, pady=5)

        self.cancel_button = ttk.Button(frame, text="Cancelar", command=self.destroy)
        self.cancel_button.grid(row=2, column=2, padx=5, pady=5)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Return>", self.find_next)
        self.bind("<Escape>", lambda e: self.destroy())

    def find_next(self, event=None):
        search_term = self.search_var.get()
        if not search_term:
            return

        self.text_widget.tag_remove("search_highlight", "1.0", tk.END)
        start_pos = self.text_widget.index(tk.INSERT)
        pos = self.text_widget.search(search_term, start_pos, stopindex=tk.END, nocase=not self.case_var.get())

        if not pos:
            pos = self.text_widget.search(search_term, "1.0", stopindex=tk.END, nocase=not self.case_var.get())
            if not pos:
                messagebox.showinfo("Buscar", f"No se encontró '{search_term}'", parent=self)
                return

        end_pos = f"{pos}+{len(search_term)}c"
        self.text_widget.tag_add("search_highlight", pos, end_pos)
        self.text_widget.mark_set(tk.INSERT, end_pos)
        self.text_widget.see(pos)
        self.text_widget.focus_set()


class SerialLoggerApp(tk.Tk):
    VERSION = "2.0.1"
    THEME_COLORS = {
        "light": {"bg": "white", "fg": "black", "cursor": "black", "sent_fg": "#00008B", "error_fg": "red",
                  "ctrl_fg": "#008080", "search_bg": "#FFFF00"},
        "dark": {"bg": "#2b2b2b", "fg": "#d8d8d8", "cursor": "white", "sent_fg": "#8ab4f8", "error_fg": "#ff7b72",
                 "ctrl_fg": "#499C98", "search_bg": "#565600"}
    }
    CONTROL_CHAR_MAP = {'\r': '[CR]', '\n': '[LF]\n', '\t': '[TAB]'}
    DECODERS: Dict[str, Callable[[Union[str, bytes]], str]] = {
        "NMEA-0183": parse_nmea_sentence,
        "Modbus-RTU": parse_modbus_rtu,
        "CAN-ASCII": parse_can_ascii,
        "JSON-line": parse_json_line
    }

    def __init__(self):
        super().__init__()
        # --- Variables y colas ---
        self.gui_queue: queue.Queue[bytes] = queue.Queue()
        self.log_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self.decoded_queue: queue.Queue[str] = queue.Queue()
        self.handler = SerialHandler(output_callback=self.gui_queue.put)
        self.log_writer_thread: Optional[threading.Thread] = None
        self.print_log_flag = False
        self.start_of_line = True
        self.command_history: Deque[str] = deque(maxlen=50)
        self.history_index = -1
        self.periodic_send_id: Optional[str] = None

        # --- Variables de estado de la GUI ---
        self.show_control_chars_var = tk.BooleanVar(value=True)  # <-- Nueva variable

        # --- Configuración ---
        self.app_config = configparser.ConfigParser()
        self.config_file = Path("settings.ini")
        self.std_logfile_name = Path.home() / "serial.log"

        self.title(f"SerialLogger v{self.VERSION}")
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'serial.png')
            self.iconphoto(True, tk.PhotoImage(file=icon_path))
        except tk.TclError:
            print("Advertencia: No se pudo encontrar 'serial.png'.")

        self._load_and_set_initial_theme()
        self._create_menu()
        self._create_widgets()
        self._load_settings()

        self.after(10, lambda: self._apply_theme(self.current_theme))
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind_all("<Control-f>", self._open_search_dialog)
        self.update_port_list()

    def _set_windows_title_bar_color(self, theme_name: str) -> None:
        if not IS_WINDOWS: return
        try:
            hwnd = wintypes.HWND(self.winfo_id())
            value = ctypes.c_int(1 if theme_name == "dark" else 0)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value),
                                                       ctypes.sizeof(value))
        except Exception as e:
            print(f"No se pudo cambiar el color de la barra de título: {e}")

    def _create_menu(self):
        self.menu_bar = tk.Menu(self)
        self.config(menu=self.menu_bar)

        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Archivo", menu=file_menu)
        file_menu.add_command(label="Guardar Buffer...", command=self._save_buffer, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="Salir", command=self._on_closing)
        self.bind_all("<Control-s>", lambda e: self._save_buffer())

        edit_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Editar", menu=edit_menu)
        edit_menu.add_command(label="Buscar...", command=self._open_search_dialog, accelerator="Ctrl+F")
        edit_menu.add_separator()
        edit_menu.add_command(label="Limpiar Salida", command=self._clear_output)

        self.always_on_top_var = tk.BooleanVar(value=False)
        view_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Ver", menu=view_menu)
        view_menu.add_checkbutton(label="Siempre Encima", onvalue=True, offvalue=False, variable=self.always_on_top_var,
                                  command=self._toggle_always_on_top)
        view_menu.add_separator()
        view_menu.add_command(label="Cambiar Tema", command=self._toggle_theme)

        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        help_menu.add_command(label="Info", command=self._show_info)

    def _create_widgets(self):
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))
        left_panel = self._create_left_panel(main_pane)
        main_pane.add(left_panel, weight=3)
        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=1)

    def _create_left_panel(self, parent):
        left_panel = ttk.Frame(parent)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(2, weight=1)

        port_frame = ttk.Frame(left_panel)
        port_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        port_frame.columnconfigure(1, weight=1)
        ttk.Label(port_frame, text="CommPort:").grid(row=0, column=0, padx=(0, 5))
        self.cb_commport = ttk.Combobox(port_frame, width=20)
        self.cb_commport.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        self.bt_update = ttk.Button(port_frame, text="Actualizar", command=self.update_port_list)
        self.bt_update.grid(row=0, column=2, padx=(0, 5))
        self.bt_clear = ttk.Button(port_frame, text="Limpiar", command=self._clear_output)  # <-- Botón restaurado
        self.bt_clear.grid(row=0, column=3)

        output_header_frame = ttk.Frame(left_panel)
        output_header_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(output_header_frame, text="Protocolo:").pack(side="left", padx=(0, 5))
        self.protocol_selector_var = tk.StringVar(value="None")
        self.cb_protocol_selector = ttk.Combobox(output_header_frame, textvariable=self.protocol_selector_var,
                                                 values=["None"] + list(self.DECODERS.keys()), state="readonly",
                                                 width=15)
        self.cb_protocol_selector.pack(side="left")

        self.autoscroll_var = tk.BooleanVar(value=True)
        self.ck_autoscroll = ttk.Checkbutton(output_header_frame, text="Auto-scroll", variable=self.autoscroll_var)
        self.ck_autoscroll.pack(side="right")
        self.show_control_chars_var = tk.BooleanVar(value=True)
        self.ck_show_ctrl_chars = ttk.Checkbutton(output_header_frame, text="Ver Chars. Ctrl",
                                                  variable=self.show_control_chars_var)  # <-- Nuevo checkbox
        self.ck_show_ctrl_chars.pack(side="right", padx=5)
        self.hex_view_var = tk.BooleanVar()
        self.ck_hex_view = ttk.Checkbutton(output_header_frame, text="Vista HEX", variable=self.hex_view_var)
        self.ck_hex_view.pack(side="right")

        self.output_notebook = ttk.Notebook(left_panel)
        self.output_notebook.grid(row=2, column=0, sticky="nsew")
        self.ta_log_panel, _ = self._create_text_panel(self.output_notebook, "Salida Raw")
        self.ta_decoded_panel, _ = self._create_text_panel(self.output_notebook, "Protocolo Decodificado")

        input_frame = ttk.LabelFrame(left_panel, text="Entrada de Comandos", padding=5)
        input_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        input_frame.columnconfigure(0, weight=1)

        input_line_frame = ttk.Frame(input_frame)
        input_line_frame.grid(row=0, column=0, sticky="ew")
        input_line_frame.columnconfigure(0, weight=1)

        self.entry_input = ttk.Entry(input_line_frame, state='disabled')
        self.entry_input.grid(row=0, column=0, sticky="ew")
        self.entry_input.bind("<Return>", self._send_data)
        self.entry_input.bind("<Up>", self._history_up)
        self.entry_input.bind("<Down>", self._history_down)

        self.hex_input_var = tk.BooleanVar(value=False)
        self.ck_hex_input = ttk.Checkbutton(input_line_frame, text="Input en HEX", variable=self.hex_input_var)
        self.ck_hex_input.grid(row=0, column=1, padx=5)

        self.line_ending_var = tk.StringVar(value="CR+LF (\\r\\n)")
        self.cb_line_ending = ttk.Combobox(input_line_frame, textvariable=self.line_ending_var,
                                           values=["None", "NL (\\n)", "CR (\\r)", "CR+LF (\\r\\n)"], width=15,
                                           state="readonly")
        self.cb_line_ending.grid(row=0, column=2, padx=5)

        self.bt_send = ttk.Button(input_line_frame, text="Enviar", state='disabled', command=self._send_data)
        self.bt_send.grid(row=0, column=3)

        periodic_frame = ttk.Frame(input_frame)
        periodic_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.repeat_send_var = tk.BooleanVar(value=False)
        self.ck_repeat_send = ttk.Checkbutton(periodic_frame, text="Repetir cada", variable=self.repeat_send_var,
                                              state='disabled')
        self.ck_repeat_send.pack(side="left")
        self.entry_repeat_ms = ttk.Entry(periodic_frame, width=8, state='disabled')
        self.entry_repeat_ms.insert(0, "1000")
        self.entry_repeat_ms.pack(side="left", padx=(0, 2))
        ttk.Label(periodic_frame, text="ms").pack(side="left", padx=(0, 5))
        self.bt_toggle_repeat = ttk.Button(periodic_frame, text="Iniciar", state='disabled',
                                           command=self._toggle_periodic_send)
        self.bt_toggle_repeat.pack(side="left")

        log_file_frame = ttk.LabelFrame(left_panel, text="Log a Archivo", padding=5)
        log_file_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        log_file_frame.columnconfigure(1, weight=1)
        self.ck_logfile_var = tk.BooleanVar()
        self.ck_logfile = ttk.Checkbutton(log_file_frame, text="Activar log a:", variable=self.ck_logfile_var)
        self.ck_logfile.grid(row=0, column=0, sticky="w")
        self.tf_logfile = ttk.Entry(log_file_frame)
        self.tf_logfile.grid(row=0, column=1, sticky="ew", padx=5)
        self.bt_fileselector = ttk.Button(log_file_frame, text="...", width=4, command=self._choose_logfile)
        self.bt_fileselector.grid(row=0, column=2, sticky="e")

        return left_panel

    def _create_right_panel(self, parent):
        right_panel = ttk.Frame(parent, width=250)
        right_panel.grid_propagate(False)
        right_panel.columnconfigure(0, weight=1)
        top_buttons_frame = ttk.Frame(right_panel)
        top_buttons_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.dtr_var = tk.BooleanVar(value=True)
        self.ck_dtr = ttk.Checkbutton(top_buttons_frame, text="DTR Auto-Reset", variable=self.dtr_var)
        self.ck_dtr.pack(side="left", fill="x", expand=True)
        params_frame = ttk.LabelFrame(right_panel, text="Parámetros Serie")
        params_frame.grid(row=1, column=0, sticky="ew")
        params_frame.columnconfigure(1, weight=1)
        self.cb_baud = self._create_param_combobox(params_frame, "Baud:", 0,
                                                   ["300", "600", "1200", "2400", "4800", "9600", "19200", "38400",
                                                    "57600", "115200"])
        self.cb_databits = self._create_param_combobox(params_frame, "Data Bits:", 1, ["5", "6", "7", "8"],
                                                       readonly=True)
        self.cb_stopbits = self._create_param_combobox(params_frame, "Stop Bits:", 2, ["1", "1.5", "2"], readonly=True)
        self.cb_parity = self._create_param_combobox(params_frame, "Paridad:", 3,
                                                     ["none", "even", "odd", "mark", "space"], readonly=True)
        self.cb_handshake = self._create_param_combobox(params_frame, "Handshake:", 4, ["none", "RTS/CTS", "XON/XOFF"],
                                                        readonly=True)
        self.encoding_var = tk.StringVar(value='utf-8')
        ttk.Label(params_frame, text="Encoding:").grid(row=5, column=0, sticky="w", padx=5, pady=2)
        self.cb_encoding = ttk.Combobox(params_frame, textvariable=self.encoding_var, width=15,
                                        values=['utf-8', 'ascii', 'latin-1', 'cp1252'])
        self.cb_encoding.grid(row=5, column=1, sticky="ew", padx=5, pady=2)
        ts_frame = ttk.LabelFrame(right_panel, text="Timestamp")
        ts_frame.grid(row=2, column=0, sticky="ew", pady=10)
        ts_frame.columnconfigure(1, weight=1)
        self.cb_timestamp = self._create_param_combobox(ts_frame, "Formato:", 0,
                                                        ["none", "ISO 8601", "Date|Time|Timezone", "Date|Time", "Time",
                                                         "Mod. Julian Date", "Year|Day of year|Time",
                                                         "yyyy|MM|dd|HH|mm|ss"], readonly=True)
        self.cb_timestamp.bind("<<ComboboxSelected>>", self._toggle_delimiter)
        self.cb_delimiter = self._create_param_combobox(ts_frame, "Delimitador:", 1,
                                                        ["blank", "komma", "semicolon", "none"], readonly=True)
        self.bt_open_port = ttk.Button(right_panel, text="Abrir Puerto", command=self.open_port)
        self.bt_open_port.grid(row=3, column=0, sticky="ew", pady=(10, 5))
        self.bt_close_port = ttk.Button(right_panel, text="Cerrar Puerto", command=self.close_port, state="disabled")
        self.bt_close_port.grid(row=4, column=0, sticky="ew")

        return right_panel

    def _create_param_combobox(self, parent, label_text, row, values, readonly=False):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", padx=5, pady=2)
        combo = ttk.Combobox(parent, width=15, values=values, state="readonly" if readonly else "normal")
        combo.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
        return combo

    def _create_text_panel(self, parent, text_label):
        frame = ttk.Frame(parent)
        parent.add(frame, text=text_label)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text_widget = tk.Text(frame, wrap='word', state='disabled', height=15, relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.config(yscrollcommand=scroll.set)
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        return text_widget, scroll

    def _load_and_set_initial_theme(self):
        if not self.config_file.exists():
            self.current_theme = "light"
        else:
            try:
                self.app_config.read(self.config_file)
                self.current_theme = self.app_config.get(Cfg.SEC_UI, Cfg.KEY_THEME, fallback="light")
            except configparser.Error:
                self.current_theme = "light"
        if SV_TTK_AVAILABLE:
            sv_ttk.set_theme(self.current_theme)

    def _apply_theme(self, theme_name: str):
        if not hasattr(self, 'ta_log_panel'): return
        self.current_theme = theme_name
        if SV_TTK_AVAILABLE:
            sv_ttk.set_theme(theme_name)
        colors = self.THEME_COLORS[theme_name]
        for panel in [self.ta_log_panel, self.ta_decoded_panel]:
            panel.config(background=colors["bg"], foreground=colors["fg"], insertbackground=colors["cursor"])
            panel.tag_config("sent", foreground=colors["sent_fg"])
            panel.tag_config("received", foreground=colors["fg"])
            panel.tag_config("error", foreground=colors["error_fg"])
            panel.tag_config("control_char", foreground=colors["ctrl_fg"])
            panel.tag_config("search_highlight", background=colors["search_bg"])
        self._set_windows_title_bar_color(theme_name)

    def _toggle_theme(self):
        new_theme = "dark" if self.current_theme == "light" else "light"
        self._apply_theme(new_theme)

    def _toggle_always_on_top(self):
        self.attributes("-topmost", self.always_on_top_var.get())

    def _open_search_dialog(self, event=None):
        SearchDialog(self, self.ta_log_panel)

    def _send_data(self, event=None) -> None:
        if 'disabled' in str(self.bt_send.cget('state')): return
        data_to_send_str = self.entry_input.get()
        if not data_to_send_str: return

        if not self.command_history or self.command_history[-1] != data_to_send_str:
            self.command_history.append(data_to_send_str)
        self.history_index = len(self.command_history)

        data_to_write: Union[str, bytes]
        success = False
        message: Optional[str] = ""

        if self.hex_input_var.get():
            try:
                clean_hex = "".join(data_to_send_str.split())
                data_to_write = bytes.fromhex(clean_hex)
                success, message = self.handler.write_data(data_to_write)
                if success: self._display_text(data_to_write, None, "sent")
            except ValueError:
                messagebox.showerror("Error de Formato", "El texto introducido no es hexadecimal válido.")
                return
        else:
            line_ending_map = {"None": "", "NL (\\n)": "\n", "CR (\\r)": "\r", "CR+LF (\\r\\n)": "\r\n"}
            line_ending = line_ending_map.get(self.line_ending_var.get(), "")
            data_to_write = data_to_send_str + line_ending
            success, message = self.handler.write_data(data_to_write, self.encoding_var.get())
            if success: self._display_text(None, data_to_write, "sent")

        if success:
            self.entry_input.delete(0, tk.END)
        else:
            if message: messagebox.showerror("Error de Envío", message)

    def _toggle_periodic_send(self):
        if self.periodic_send_id:
            self.after_cancel(self.periodic_send_id)
            self.periodic_send_id = None
            self.bt_toggle_repeat.config(text="Iniciar")
            self.entry_input.config(state="normal")
        else:
            try:
                interval = int(self.entry_repeat_ms.get())
                if interval < 20:
                    messagebox.showwarning("Intervalo Inválido", "El intervalo debe ser de al menos 20 ms.")
                    return
                self.bt_toggle_repeat.config(text="Detener")
                self.entry_input.config(state="disabled")
                self._periodic_send_task(interval)
            except ValueError:
                messagebox.showerror("Error de Intervalo", "El intervalo debe ser un número entero.")

    def _periodic_send_task(self, interval_ms: int):
        self._send_data()
        self.periodic_send_id = self.after(interval_ms, self._periodic_send_task, interval_ms)

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

    def _load_settings(self):
        self._set_defaults()
        if not self.config_file.exists(): return

        self.app_config.read(self.config_file)

        is_maximized = self.app_config.getboolean(Cfg.SEC_WINDOW, Cfg.KEY_MAXIMIZED, fallback=False)
        if is_maximized:
            self.state('zoomed')
        else:
            self.geometry(self.app_config.get(Cfg.SEC_WINDOW, Cfg.KEY_GEOMETRY, fallback="1100x700+100+100"))
        self.always_on_top_var.set(self.app_config.getboolean(Cfg.SEC_WINDOW, Cfg.KEY_TOPMOST, fallback=False))
        self._toggle_always_on_top()

        self.cb_baud.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_BAUD, fallback="9600"))
        self.cb_databits.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_DATABITS, fallback="8"))
        self.cb_stopbits.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_STOPBITS, fallback="1"))
        self.cb_parity.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_PARITY, fallback="none"))
        self.cb_handshake.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_HANDSHAKE, fallback="none"))
        self.dtr_var.set(self.app_config.getboolean(Cfg.SEC_SERIAL, Cfg.KEY_DTR_RESET, fallback=True))
        self.encoding_var.set(self.app_config.get(Cfg.SEC_SERIAL, Cfg.KEY_ENCODING, fallback='utf-8'))

        self.cb_timestamp.set(self.app_config.get(Cfg.SEC_LOG, Cfg.KEY_TIMESTAMP, fallback="none"))
        self.cb_delimiter.set(self.app_config.get(Cfg.SEC_LOG, Cfg.KEY_DELIMITER, fallback="blank"))
        self.tf_logfile.delete(0, tk.END)
        self.tf_logfile.insert(0, self.app_config.get(Cfg.SEC_LOG, Cfg.KEY_FILE, fallback=str(self.std_logfile_name)))
        self.ck_logfile_var.set(self.app_config.getboolean(Cfg.SEC_LOG, Cfg.KEY_ENABLED, fallback=False))

        self.line_ending_var.set(self.app_config.get(Cfg.SEC_UI, Cfg.KEY_LINE_ENDING, fallback="CR+LF (\\r\\n)"))
        self.hex_view_var.set(self.app_config.getboolean(Cfg.SEC_UI, Cfg.KEY_HEX_VIEW, fallback=False))
        self.hex_input_var.set(self.app_config.getboolean(Cfg.SEC_UI, Cfg.KEY_HEX_INPUT, fallback=False))
        self.protocol_selector_var.set(self.app_config.get(Cfg.SEC_UI, Cfg.KEY_PROTOCOL, fallback="None"))
        self.autoscroll_var.set(self.app_config.getboolean(Cfg.SEC_UI, Cfg.KEY_AUTOSCROLL, fallback=True))
        self.show_control_chars_var.set(
            self.app_config.getboolean(Cfg.SEC_UI, Cfg.KEY_SHOW_CTRL_CHARS, fallback=True))  # <-- Cargar nuevo ajuste

        self._toggle_delimiter()

    def _save_settings(self):
        if not self.app_config.has_section(Cfg.SEC_WINDOW): self.app_config.add_section(Cfg.SEC_WINDOW)
        is_maximized = self.state() == 'zoomed'
        self.app_config.set(Cfg.SEC_WINDOW, Cfg.KEY_MAXIMIZED, str(is_maximized))
        if not is_maximized: self.app_config.set(Cfg.SEC_WINDOW, Cfg.KEY_GEOMETRY, self.geometry())
        self.app_config.set(Cfg.SEC_WINDOW, Cfg.KEY_TOPMOST, str(self.always_on_top_var.get()))

        if not self.app_config.has_section(Cfg.SEC_SERIAL): self.app_config.add_section(Cfg.SEC_SERIAL)
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_BAUD, self.cb_baud.get())
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_DATABITS, self.cb_databits.get())
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_STOPBITS, self.cb_stopbits.get())
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_PARITY, self.cb_parity.get())
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_HANDSHAKE, self.cb_handshake.get())
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_DTR_RESET, str(self.dtr_var.get()))
        self.app_config.set(Cfg.SEC_SERIAL, Cfg.KEY_ENCODING, self.encoding_var.get())

        if not self.app_config.has_section(Cfg.SEC_LOG): self.app_config.add_section(Cfg.SEC_LOG)
        self.app_config.set(Cfg.SEC_LOG, Cfg.KEY_TIMESTAMP, self.cb_timestamp.get())
        self.app_config.set(Cfg.SEC_LOG, Cfg.KEY_DELIMITER, self.cb_delimiter.get())
        self.app_config.set(Cfg.SEC_LOG, Cfg.KEY_FILE, self.tf_logfile.get())
        self.app_config.set(Cfg.SEC_LOG, Cfg.KEY_ENABLED, str(self.ck_logfile_var.get()))

        if not self.app_config.has_section(Cfg.SEC_UI): self.app_config.add_section(Cfg.SEC_UI)
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_THEME, self.current_theme)
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_LINE_ENDING, self.line_ending_var.get())
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_HEX_VIEW, str(self.hex_view_var.get()))
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_HEX_INPUT, str(self.hex_input_var.get()))
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_PROTOCOL, self.protocol_selector_var.get())
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_AUTOSCROLL, str(self.autoscroll_var.get()))
        self.app_config.set(Cfg.SEC_UI, Cfg.KEY_SHOW_CTRL_CHARS,
                            str(self.show_control_chars_var.get()))  # <-- Guardar nuevo ajuste

        with open(self.config_file, 'w') as configfile:
            self.app_config.write(configfile)

    def open_port(self):
        self._clear_output()
        config = {
            'port': self.cb_commport.get(), 'baud': self.cb_baud.get(), 'databits': self.cb_databits.get(),
            'stopbits': self.cb_stopbits.get(), 'parity': self.cb_parity.get(), 'handshake': self.cb_handshake.get(),
            'dtr': self.dtr_var.get(), 'protocol': self.protocol_selector_var.get()
        }
        if not config['port']:
            messagebox.showerror("Error", "No se ha seleccionado un puerto COM.")
            return

        success, message = self.handler.open_port(config)
        if not success:
            messagebox.showerror("Error de Puerto Serie", message)
            return

        self.start_of_line = True
        self._toggle_connection_state(connected=True)
        self.after(50, self._process_gui_queue)

        if self.ck_logfile_var.get() or self.protocol_selector_var.get() != "None":
            self.print_log_flag = True
            self.log_writer_thread = threading.Thread(target=self._log_and_decode_task, daemon=True)
            self.log_writer_thread.start()
            self.after(50, self._process_decoded_queue)

    def close_port(self):
        if self.periodic_send_id: self._toggle_periodic_send()
        self.handler.close_port()
        self.log_queue.put(None)
        self._toggle_connection_state(connected=False)

    def _toggle_connection_state(self, connected: bool):
        state_if_disconnected = "normal"
        state_if_connected = "disabled"
        for widget in [self.cb_commport, self.bt_update, self.cb_baud, self.cb_databits, self.ck_dtr,
                       self.cb_stopbits, self.cb_parity, self.cb_handshake, self.cb_encoding,
                       self.cb_protocol_selector, self.tf_logfile, self.bt_fileselector, self.ck_logfile]:
            widget.config(state=state_if_connected if connected else "normal")
        for widget in [self.cb_databits, self.cb_stopbits, self.cb_parity, self.cb_handshake,
                       self.cb_protocol_selector]:
            widget.config(state=state_if_connected if connected else "readonly")
        self.bt_open_port.config(state=state_if_connected if connected else "normal")
        self.bt_close_port.config(state="normal" if connected else "disabled")
        self.entry_input.config(state="normal" if connected else "disabled")
        self.ck_hex_input.config(state="normal" if connected else "disabled")
        self.cb_line_ending.config(state="readonly" if connected else "disabled")
        self.bt_send.config(state="normal" if connected else "disabled")
        self.ck_repeat_send.config(state="normal" if connected else "disabled")
        self.entry_repeat_ms.config(state="normal" if connected else "disabled")
        self.bt_toggle_repeat.config(state="normal" if connected else "disabled")

    def _display_text(self, raw_data: Optional[bytes], text_data: Optional[str], tag: str):
        self.ta_log_panel.config(state='normal')
        is_hex = self.hex_view_var.get()
        encoding = self.encoding_var.get()
        display_string = ""
        if raw_data:
            if is_hex or self.protocol_selector_var.get() == "Modbus-RTU":
                display_string = ' '.join(f'{b:02X}' for b in raw_data) + ' '
            else:
                display_string = codecs.getincrementaldecoder(encoding)(errors='replace').decode(raw_data)
        elif text_data:
            if is_hex:
                display_string = ' '.join(f'{ord(c):02X}' for c in text_data if ord(c) <= 255) + ' '
            else:
                display_string = text_data

        for char in display_string:
            if self.start_of_line:
                timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                if timestamp: self.ta_log_panel.insert(tk.END, timestamp, "received")
                self.start_of_line = False

            # Lógica modificada para el nuevo checkbox
            if self.show_control_chars_var.get() and char in self.CONTROL_CHAR_MAP and not is_hex:
                self.ta_log_panel.insert(tk.END, self.CONTROL_CHAR_MAP[char], ("control_char", tag))
            else:
                self.ta_log_panel.insert(tk.END, char, tag)

            if char == '\n' or (self.protocol_selector_var.get() == "Modbus-RTU" and tag == "received"):
                self.start_of_line = True

        if self.autoscroll_var.get():
            self.ta_log_panel.see(tk.END)
        self.ta_log_panel.config(state='disabled')

    def _process_decoded_queue(self):
        try:
            while not self.decoded_queue.empty():
                decoded_line = self.decoded_queue.get_nowait()
                self.ta_decoded_panel.config(state='normal')
                self.ta_decoded_panel.insert(tk.END, decoded_line)
                if self.autoscroll_var.get():
                    self.ta_decoded_panel.see(tk.END)
                self.ta_decoded_panel.config(state='disabled')
        except queue.Empty:
            pass
        if self.handler.is_reading.is_set(): self.after(50, self._process_decoded_queue)

    def _set_defaults(self):
        self.cb_baud.set("9600");
        self.cb_databits.set("8");
        self.cb_stopbits.set("1")
        self.cb_parity.set("none");
        self.cb_handshake.set("none")
        self.cb_timestamp.set("none");
        self.cb_delimiter.set("blank")
        self.tf_logfile.insert(0, str(self.std_logfile_name))
        self.ck_logfile_var.set(False)
        self._toggle_delimiter()
        self.encoding_var.set('utf-8')
        self.protocol_selector_var.set("None")
        self.show_control_chars_var.set(True)  # <-- Valor por defecto

    def _on_closing(self):
        buffer_content = self.ta_log_panel.get("1.0", "end-1c")
        if buffer_content and not self.print_log_flag:
            response = messagebox.askyesnocancel("Buffer no guardado",
                                                 "El buffer contiene datos sin guardar.\n¿Desea guardarlos antes de salir?")
            if response is True:
                if not self._save_buffer(): return
            elif response is None:
                return
        self._save_settings()
        if self.handler.is_reading.is_set(): self.close_port()
        self.destroy()

    def _show_info(self):
        messagebox.showinfo("Info",
                            f"SerialLogger v{self.VERSION}\n\n"
                            "Utilitario serie con GUI y CLI.\n"
                            "Novedades v2.0.1:\n"
                            "- Botón 'Limpiar' restaurado\n"
                            "- Opción para visualizar/ocultar chars de control\n\n"
                            f"(c) 2013-{get_current_year()} Hani Ibrahim\n"
                            "Traducción y mejoras por AI\n\n"
                            "Licencia: GNU Public License 3.0")

    def _log_and_decode_task(self):
        print("Hilo de log/decode iniciado.")
        line_buffer = '';
        decoder = codecs.getincrementaldecoder(self.encoding_var.get())(errors='replace')
        log_to_file = self.ck_logfile_var.get();
        protocol = self.protocol_selector_var.get()
        parser = self.DECODERS.get(protocol)
        logfile = None
        if log_to_file:
            log_filename = self.tf_logfile.get()
            try:
                logfile = open(log_filename, 'a', encoding='utf-8')
            except IOError as e:
                print(f"Error al escribir en el log: {e}"); log_to_file = False
        while True:
            raw_chunk = self.log_queue.get()
            if raw_chunk is None: break
            if protocol == "Modbus-RTU":
                if parser:
                    decoded_line = parser(raw_chunk)
                    self.decoded_queue.put(decoded_line)
                    if log_to_file and logfile:
                        timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                        logfile.write(f"{timestamp}{decoded_line}")
                continue
            decoded_string = decoder.decode(raw_chunk)
            line_buffer += decoded_string
            while '\n' in line_buffer:
                line, line_buffer = line_buffer.split('\n', 1)
                full_line = line + '\n'
                if log_to_file and logfile:
                    timestamp = get_timestamp(self.cb_timestamp.get(), self.cb_delimiter.get())
                    logfile.write(f"{timestamp}{line}\n");
                    logfile.flush()
                if parser:
                    decoded_line = parser(full_line)
                    self.decoded_queue.put(decoded_line)
        if logfile: logfile.close()
        print("Hilo de log/decode terminado.")

    def _clear_output(self):
        self.ta_log_panel.config(state='normal');
        self.ta_log_panel.delete('1.0', tk.END)
        self.ta_decoded_panel.config(state='normal');
        self.ta_decoded_panel.delete('1.0', tk.END)
        self.ta_log_panel.config(state='disabled');
        self.ta_decoded_panel.config(state='disabled')
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

    def update_port_list(self):
        ports = serial.tools.list_ports.comports()
        port_names = [port.device for port in ports]
        self.cb_commport['values'] = port_names
        if port_names:
            self.cb_commport.set(port_names[0])
        else:
            self.cb_commport.set("")

    def _process_gui_queue(self):
        try:
            while not self.gui_queue.empty():
                raw_chunk = self.gui_queue.get_nowait()
                if self.ck_logfile_var.get() or self.protocol_selector_var.get() != "None": self.log_queue.put(
                    raw_chunk)
                self._display_text(raw_chunk, None, "received")
        except queue.Empty:
            pass
        if self.handler.is_reading.is_set(): self.after(50, self._process_gui_queue)

    def _toggle_delimiter(self, event=None):
        if not hasattr(self, 'cb_timestamp'): return
        is_disabled = 'disabled' in str(self.cb_timestamp.cget('state'))
        if self.cb_timestamp.get() == "none" or is_disabled:
            self.cb_delimiter.config(state="disabled")
        else:
            self.cb_delimiter.config(state="readonly")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"SerialLogger v{SerialLoggerApp.VERSION} - Monitor de Puerto Serie con GUI y CLI.")
    parser.add_argument('--no-gui', action='store_true', help="Forzar la ejecución en modo consola (CLI).")
    parser.add_argument('-p', '--port', type=str, help="Puerto serie a usar (ej. COM3, /dev/ttyUSB0).")
    parser.add_argument('-b', '--baud', type=int, default=9600, help="Baud rate (defecto: 9600).")
    parser.add_argument('--databits', type=int, choices=[5, 6, 7, 8], default=8, help="Bits de datos (defecto: 8).")
    parser.add_argument('--stopbits', type=float, choices=[1, 1.5, 2], default=1, help="Bits de parada (defecto: 1).")
    parser.add_argument('--parity', type=str, choices=['none', 'even', 'odd', 'mark', 'space'], default='none',
                        help="Paridad (defecto: none).")
    parser.add_argument('--no-dtr', action='store_true', help="Desactivar el auto-reset por DTR (útil para Arduinos).")
    parser.add_argument('--encoding', type=str, default='utf-8',
                        help="Codificación de caracteres a usar (ej. utf-8, ascii, latin-1).")
    parser.add_argument('--timestamp', type=str, default='none',
                        choices=["none", "ISO 8601", "Date|Time|Timezone", "Date|Time", "Time"],
                        help="Formato de timestamp a usar en la consola.")
    parser.add_argument('-l', '--log', type=str, help="Ruta al archivo para guardar el log.")
    args = parser.parse_args()
    if args.port or args.no_gui:
        if not args.port: parser.error("--port es requerido para el modo CLI.")
        run_cli_mode(args)
    else:
        app = SerialLoggerApp()
        app.mainloop()