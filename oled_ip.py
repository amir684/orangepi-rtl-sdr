import socket
import time
from smbus2 import SMBus, i2c_msg
from PIL import Image, ImageDraw, ImageFont

ADDR = 0x3C
BUS = 3

def cmd(bus, *commands):
    msg = i2c_msg.write(ADDR, [0x00] + list(commands))
    bus.i2c_rdwr(msg)

def init_display(bus):
    for c in [0xAE,0xD5,0x80,0xA8,0x1F,0xD3,0x00,0x40,
              0x8D,0x14,0x20,0x00,0xA1,0xC8,0xDA,0x02,
              0x81,0xCF,0xD9,0xF1,0xDB,0x40,0xA4,0xA6,0xAF]:
        cmd(bus, c)

def display_image(bus, image):
    img = image.convert('1')
    pixels = list(img.getdata())
    cmd(bus, 0x21, 0, 127)
    cmd(bus, 0x22, 0, 3)
    buf = []
    for page in range(4):
        for col in range(128):
            byte = 0
            for bit in range(8):
                row = page * 8 + bit
                if row < 32 and pixels[row * 128 + col] != 0:
                    byte |= (1 << bit)
            buf.append(byte)
    for i in range(0, len(buf), 16):
        msg = i2c_msg.write(ADDR, [0x40] + buf[i:i+16])
        bus.i2c_rdwr(msg)

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "No IP"

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
bus = SMBus(BUS)
init_display(bus)

while True:
    ip = get_ip()
    img = Image.new('1', (128, 32), 0)
    draw = ImageDraw.Draw(img)
    draw.text((0, 8), ip, font=font, fill=1)
    display_image(bus, img)
    time.sleep(10)
