# 主要负责三件事：
# 1. 在屏幕上播放黑白闪烁刺激
# 2. 把刺激开始时间发给脑电解码程序（发送 TIME 给 OperationMain.py）
# 3. 接收解码结果，并把结果转成无人机动作

from Config import Config
from psychopy import event, visual, core
# 这是屏幕刺激的核心库。
# visual：创建窗口、显示图片、显示文字。
# event：监听键盘，比如空格、ESC。
# core：计时，用来测量刺激播放时间。

import os
import time
from RoboMasterThread2 import RoboMasterThread2 #无人机通信类
import socket
from threading import Thread


#接收解码结果，并转成无人机动作
def receive_fcn(clientSocket, DroneThread, distance, stop_flag):
        
    BUFIZ = 1024
    clientSocket.settimeout(20000)
    while True:
        consumeMsg = clientSocket.recv(BUFIZ)
        if consumeMsg:
            message = str(consumeMsg)[2:-1]
            if len(message) > 5:
                result = int(message[5:])
                if result == 0: command = 'takeoff'
                elif result == 1: command = 'up '+str(distance)
                elif result == 2: command = 'land'
                elif result == 3: command = 'down '+str(distance)
                elif result == 4: command = 'forward '+str(distance)
                elif result == 5: command = 'right '+str(distance)
                elif result == 6: command = 'back '+str(distance)
                elif result == 7: command = 'left '+str(distance)
                elif result == 8: command = 'flip l'
                DroneThread.send(command)
                if result == 2:
                    time.sleep(1)
                    DroneThread.send('motoron')
        time.sleep(0.1)
        if stop_flag():
            print("Exiting receive_exchange_message_fcn!")
            break

def main():

    config = Config()   #创建配置对象
    # addSTI = os.path.join(os.getcwd(),'pics2')
    # backpath = os.path.join(os.getcwd(),'background.jpg')

    #读取刺激图片和背景的路径
    addSTI = os.path.join(os.getcwd(),'_internal', 'pics2')
    backpath = os.path.join(os.getcwd(),'_internal','background.jpg')
    
    window_x, window_y = config.window_size
    stop_flag = False

    #连接脑电的解码程序
    opt_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    notconnect = True
    reconnecttime = 0  # 未连接次数
    while notconnect:
        try:
            opt_sock.connect(config.client_address)
            clientSocket = opt_sock
            notconnect = False
            print('Opt conneted')
        except:
            reconnecttime += 1
            if reconnecttime > 5:
                break
            
    # Drone Thread
    DroneThread = RoboMasterThread2(roboAddress=('192.168.10.1', 8889))
    DroneThread.start()
    DroneThread.send('command')
    # 起浆：让风扇转起来，可以有效降温
    time.sleep(1)
    DroneThread.send('motoron')

    receive_message_thread = Thread(name='recieve',target=receive_fcn, args=(clientSocket, DroneThread, config.distance, lambda: stop_flag))
    receive_message_thread.start()


    # set the window
    win = visual.Window([window_x, window_y], monitor="testMonitor", units="pix", fullscr=True, waitBlanking=True, color=(0, 0, 0), colorSpace='rgb255', screen=0, allowGUI=True)
    # loading pictures
    text = 'Loading...'
    text = visual.TextStim(win, pos=[0, 0], text=text, color=(255, 255, 255), colorSpace='rgb255')
    text.draw()
    win.flip()
    
    background_image = visual.ImageStim(win, image=backpath, pos=[0, 0], size=[window_x, window_y], units='pix', flipVert=False)

    picAdd = os.listdir(addSTI)
    frameSets = []
    add = addSTI + os.sep + 'display_frame.png'
    displayFrame = visual.ImageStim(win, image=add, pos=[0, 0], size=[window_x, window_y], units='pix', flipVert=False)
        
    frameSet = []
    # stimulation frames
    for picINX in range(len(picAdd)-1):
        add = addSTI + os.sep + '%i.png' % picINX
        frame = visual.ImageStim(win, image=add, pos=[0, 0], size=[window_x, window_y], units='pix', flipVert=False)
        frameSet.append(frame)

    frameSets.append(frameSet)

    # stimulus start
    text = 'press space to begin.'
    text = visual.TextStim(win, pos=[0, 0], text=text, color=(255, 255, 255), colorSpace='rgb255')
    text.draw()
    win.flip()
    event.waitKeys(keyList=['space'])

    while not stop_flag:
        
        background_image.draw()
        displayFrame.draw()
        win.flip()
        time.sleep(2)
            
        frameINX = 0
        startTime = core.getTime()
        # one stim loop
        current_time = time.time()
        current_time_ms = int(current_time * 1000)
        message = 'TIME:'+str(current_time_ms)
        clientSocket.send(message.encode('utf-8'))
        print('Current time: '+str(current_time_ms))
        while frameINX < len(frameSet):
            background_image.draw()
            frameSet[frameINX].draw()
            win.flip()
            frameINX += 1   
                    
        endTime = core.getTime()
        print("STI ended{}".format(endTime-startTime))
        # time test
        print(time.time())

        text = 'Press space to continue.'
        text = visual.TextStim(win, pos=[0, 0], text=text, color=(255, 255, 255), colorSpace='rgb255')
        text.draw()
        win.flip()
        while True:
            keys = event.getKeys()
            if 'escape' in keys:
                stop_flag = True
                DroneThread.send('land')
                print("ESC pressed, exiting...")
                time.sleep(3)
                DroneThread.send('motoron')
                break
            elif 'space' in keys:
                break
            time.sleep(0.1)

    # 关闭窗口
    DroneThread.close()
    stop_message = 'STOP'
    clientSocket.send(stop_message.encode('utf-8'))
    win.close()
    core.quit()

if __name__ == '__main__':
    main()  # 
