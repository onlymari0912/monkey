import socket

# https://stackoverflow.com/a/28950776
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(("10.254.254.254", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


ip = get_ip()
port = 8890
response_compression = False

http_trace = True
verbose_log = False

arcade = "Ｍ０ＮＫＹＢＵＳ１Ｎ３Ｚ"
paseli = 10597
maintenance_mode = False
