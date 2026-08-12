from ND8 import NDThread
from spatialFilter import fbCCA
from Config import Config
from threading import Thread
import socket
import time
import queue

def receive_fcn(message_queue, socket):
  
    BUFIZ = 1024
    socket.settimeout(20000)
    while True:
        consumeMsg = socket.recv(BUFIZ)
        if consumeMsg:
            message = str(consumeMsg)[2:-1]
            message_queue.put(message)
            event = message[0:4]
            if event == 'STOP': 
                print("Exiting receive_exchange_message_fcn!")
                break
        time.sleep(0.1)
        
def main():
    # parameters
    config = Config()
    stop_flag = False
    message_queue = queue.Queue(0)
    # communication
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    notconnect = True
    reconnecttime = 0  
    while notconnect:
        try:
            _sock.bind(('', config.client_address[-1]))
            _sock.listen(1)
            print('Server wating for connection')
            clientSocket, clientAddr = _sock.accept()
            notconnect = False
        except:
            reconnecttime += 1
            if reconnecttime > 5:
                break

    receive_message_thread = Thread(name='recieve',target=receive_fcn, args=(message_queue, clientSocket))
    receive_message_thread.start()
    # Algorithm
    algorithm = fbCCA(srate=config.srate, frequency=config.frequency, winLEN=config.winLEN)
    algorithm.fit()
    # ND Thread
    ND_thread = NDThread(ND_address=config.ND_address, srate=config.srate, record_srate=config.record_srate)
    ND_thread.connect()
    ND_thread.start()
    # loop
    while not stop_flag:
        
        if message_queue.qsize() > 0:
            message = message_queue.get()
            if len(message) > 5:
                timetrigger = int(message[5:])
                epoch = ND_thread.readFixedData(config.winLEN+config.lag, timetrigger)
                result = algorithm.predict(epoch)
                result = result[0]
                result_massage = 'RSLT:'+str(result)
                clientSocket.send(result_massage.encode('utf-8'))
                print('send message：{0}'.format(result_massage))
            else:
                event = message[0:4]
                if event == 'STOP': 
                    stop_flag = True
                    break
            time.sleep(0.01)
            
    ND_thread.disconnect()
    clientSocket.close()

if __name__ == '__main__':
    main()  # 
                
    

