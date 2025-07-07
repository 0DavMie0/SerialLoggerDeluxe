# SerialLogger - Monitor de Puerto Serie Avanzado

<p align="center">
  <!-- RECUERDA REEMPLAZAR ESTA LÍNEA CON EL ENLACE A TU CAPTURA DE PANTALLA -->
  <img src="https://raw.githubusercontent.com/0DavMie0/SerialLoggerDeluxe/refs/heads/master/SerialLoggerGUI.png" alt="Captura de pantalla de SerialLogger" width="750"/>
</p>

**SerialLogger** es una herramienta multiplataforma, escrita en Python, diseñada para la monitorización, depuración y comunicación con dispositivos de puerto serie, como Arduino, ESP32 y otros microcontroladores.

Este proyecto es una conversión y una mejora significativa de la aplicación original [SerialLogger en Java](https://github.com/hani-ibrahim/serial-logger) de Hani Andreas Ibrahim, modernizada y ampliada con la asistencia de la IA de Google.

## Características Principales

Esta versión combina la robustez de una herramienta de línea de comandos (CLI) con la facilidad de uso de una interfaz gráfica (GUI), ofreciendo lo mejor de ambos mundos.

### Interfaz Gráfica (GUI)

Ejecuta el script sin argumentos para lanzar la interfaz gráfica, que incluye:

*   **Detección Automática de Puertos**: Escanea y lista los puertos serie disponibles.
*   **Configuración Completa**: Ajusta Baud Rate, Data Bits, Stop Bits, Parity, Handshake (RTS/CTS, XON/XOFF) y Encoding.
*   **Visor de Salida en Tiempo Real**: Muestra los datos recibidos carácter a carácter.
*   **Input Interactivo**: Envía comandos al dispositivo con control de terminador de línea (`None`, `LF`, `CR`, `CR+LF`).
*   **Historial de Comandos**: Navega por los últimos comandos enviados con las teclas de flecha arriba/abajo.
*   **Temas Oscuro y Claro**: Cambia entre un tema claro y uno oscuro con un solo clic. La aplicación recuerda tu preferencia.
*   **Barra de Título Nativa**: La barra de título de la ventana se adapta al tema oscuro/claro en Windows.
*   **Vista HEX**: Alterna entre la visualización de texto y la representación hexadecimal de los datos.
*   **Control DTR (Auto-Reset)**: Activa o desactiva la línea DTR, útil para evitar el reinicio de placas como Arduino UNO.
*   **Timestamps Configurables**: Añade un timestamp personalizable al inicio de cada línea en la salida.
*   **Logging a Fichero**: Guarda toda la sesión de comunicación en un archivo de log.
*   **Persistencia de Sesión**: La aplicación guarda toda tu configuración (tamaño de ventana, estado de maximización, parámetros del puerto, tema, etc.) para la próxima vez que la abras.

### Interfaz de Línea de Comandos (CLI)

Ideal para scripting, automatización o para trabajar en entornos sin escritorio.

*   **Control Total**: Todos los parámetros de la conexión (puerto, baudrate, etc.) se pueden especificar mediante argumentos.
*   **Salida Directa a Consola**: Monitoriza el flujo de datos directamente en tu terminal.
*   **Logging a Fichero**: Redirige la salida a un archivo de log, igual que en la GUI.

## Requisitos

*   Python 3.7 o superior.
*   Las bibliotecas necesarias, que se pueden instalar fácilmente.

## Instalación

1.  **Clona el repositorio:**
    ```bash
    git clone https://github.com/tu_usuario/tu_repositorio.git
    cd tu_repositorio
    ```

2.  **Crea un entorno virtual (recomendado):**
    ```bash
    python -m venv venv
    # En Windows
    .\venv\Scripts\activate
    # En Linux/macOS
    source venv/bin/activate
    ```

3.  **Instala las dependencias:**
    El proyecto utiliza las siguientes bibliotecas, que se incluyen en el archivo `requirements.txt`:
    *   `pyserial`: para la comunicación serie.
    *   `sv-ttk`: para los temas de la interfaz gráfica.

    Instálalas con un solo comando:
    ```bash
    pip install -r requirements.txt
    ```

## Modo de Uso

### Uso de la Interfaz Gráfica (GUI)

Para lanzar la aplicación en modo gráfico, simplemente ejecuta el script sin argumentos:

```bash
python SerialLogger.py
```

Configura los parámetros en el panel derecho, selecciona tu puerto y haz clic en "Open Port".

**Uso de la Línea de Comandos (CLI)**
Para usar la aplicación desde la terminal, el único argumento obligatorio es el puerto (-p o --port).

# En Windows
```bash
python SerialLogger.py -p COM3
```

# En Linux/macOS
```bash
python SerialLogger.py -p /dev/ttyUSB0
```

Ejemplo completo con más opciones:
```bash
python SerialLogger.py -p COM3 -b 115200 --encoding latin-1 --timestamp "Date|Time" -l mi_sesion.log --no-dtr
```
