import threading
import time
import socket
from datetime import datetime
from threading import Thread


class RoboMasterThread2(Thread):

    _roboAddress = None
    _sock = None
    _get_info_last_time = datetime.now()

    def __init__(self, roboAddress):
        super().__init__()
        self._roboAddress = roboAddress
        self._is_running = True
        # 创建一个接收用户指令的UDP连接
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.repeat_function(3)

    def run(self):
        while self._is_running:
            try:
                # print("recvfrom start " + datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"))
                response, ip_address = self._sock.recvfrom(128)
                # print("recvfrom over " + datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"))
                response = response.decode(encoding='utf-8')
                if response == 'ok' or response == 'error':
                    print("RoboMaster Received  message: " + response)
                else:
                    print("Received message: " + response)
                time.sleep(0.01)
                
            except Exception as e:
                print("RoboMaster Error receiving: " + str(e))
                time.sleep(1)

    def send(self, message):
        try:
            print("Send message: " + message)
            self._sock.sendto(message.encode(encoding="utf-8"), self._roboAddress)
        except Exception as e:
            print("RoboMaster Error sending: " + str(e))

    def close(self):
        self._is_running = False
        self._sock.close()
        print('Drone disconnected.')

    def send_drone_info(self):
        now = datetime.now()
        if now.second % 2 == 0:
            self.send("battery?")
        else:
            # wifi信噪比
            self.send("wifi?")

    def repeat_function(self, interval):
        self.send_drone_info()
        threading.Timer(interval, self.repeat_function, [interval]).start()

