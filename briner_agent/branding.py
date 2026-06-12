"""
Identidad visual compartida: logo y paleta de colores.

Usado por el icono de bandeja de NAPBackground y por la ventana de NAPMonitor
para que ambos procesos muestren la misma marca.
"""

# Paleta base (tonos Tailwind, consistentes entre tray y monitor)
ACCENT = (37, 99, 235)      # azul  — estado normal / procesando
GREEN = (22, 163, 74)       # verde — activo sin errores
RED = (220, 38, 38)         # rojo  — error
AMBER = (217, 119, 6)       # ambar — advertencia / rate limit
SLATE = (100, 116, 139)     # gris  — inactivo


def make_logo_image(color: tuple = ACCENT, size: int = 64):
    """Logo de NAP: carpeta blanca sobre placa redondeada de color.

    Retorna un PIL.Image listo para pystray o para iconphoto de Tk.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 64.0  # factor de escala respecto al lienzo de diseno 64x64

    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    # Placa de fondo redondeada
    draw.rounded_rectangle(box(2, 2, 62, 62), radius=14 * s, fill=(*color, 255))
    # Pestana de la carpeta
    draw.rounded_rectangle(box(14, 19, 34, 31), radius=4 * s, fill=(255, 255, 255, 235))
    # Cuerpo de la carpeta
    draw.rounded_rectangle(box(14, 25, 50, 47), radius=5 * s, fill=(255, 255, 255, 255))
    # Linea divisoria sutil que sugiere "ordenado"
    draw.line(box(20, 36, 44, 36), fill=(*color, 90), width=max(1, int(2 * s)))
    return img
