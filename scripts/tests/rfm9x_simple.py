import board
import busio
import digitalio
import adafruit_rfm9x

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)     # GPIO8 / physical pin 24
reset = digitalio.DigitalInOut(board.D25)  # GPIO25 / physical pin 22

rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 915.0)
rfm9x.tx_power = 23
rfm9x.send(bytes("Hello from Buoy", "utf-8"))
