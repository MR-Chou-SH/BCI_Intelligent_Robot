#整个项目的“参数集中管理文件”。负责告诉其他文件：闪烁屏幕多大、采样率是多少、刺激时间、脑电服务连哪里、无人机连哪里、无人机移动距离是多少

import numpy as np
import os

class Config():

    def __init__(self) -> None:
        self.defaultConfig()   #创建配置对象时，程序会自动加载默认配置
        pass

    def defaultConfig(self,):

        self.displayINFO()  #屏幕参数，默认 1920x1080、60Hz
        self.expINFO()      #脑电实验/解码相关参数
        self.connectINFO()  #网络连接相关参数

        pass

    #屏幕刷新率默认 60Hz, 窗口大小默认 1920 x 1080。
    #它主要被 Drone_psycho.py) 使用, 决定了 PsychoPy 创建多大的显示窗口
    def displayINFO(self, refreshRate=60, window_size=(1920,1080)):

        self.refreshRate = refreshRate
        self.window_size = window_size
        pass


    #=======================================================
    #====================最重要的参数========================
    #=======================================================
    def expINFO(self, srate=250, record_srate = 1000, winLEN=3, lag=0.14, frequency = np.arange(8,17,1), distance=20):

        self.srate = srate  #进入解码算法使用的采样率，250Hz。
        self.record_srate = record_srate    
        #脑电设备原始记录采样率，1000Hz。
        # 也就是设备/数据服务传来的原始 EEG 可能是每秒 1000 个点。
        # 然后 ND8.py 的 preprocess() 会把它重采样到 srate=250。
    
        self.winLEN = winLEN       #解码窗口长度，3 秒。
        self.lag = lag             #脑电响应延迟，0.14 秒。
        self.frequency = frequency  #9个候选频率列表
        self.distance = None

        #从当前工作目录下的 readme.txt 读取无人机移动步长
        path = os.path.join(os.getcwd(),'readme.txt')
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
        for line in lines:
            try:
                if line[:8] == 'distance':
                    self.distance = int(line[10:])
            except:
                pass

        #如果“readme.txt”文件中没有给出distance的定义，就采用默认的20cm
        if self.distance is None: self.distance = distance
        pass


        #这段决定三个通信地址。
    def connectINFO(self, robo_address=('192.168.10.1', 8889), ND_address=('127.0.0.1',8899), client_address=('127.0.0.1', 11000)):

        self.robo_address = robo_address    #无人机地址
        self.ND_address = ND_address        #脑电数据服务地址。脑电采集线程会连接本机 8899 端口，读取 EEG 数据。
        self.client_address = client_address#两个 Python 主流程之间的本机通信地址。
        #第三个连接的是Drone_psycho.py  <----TCP---->  OperationMain.py

        pass

#单独测试入口，只运行该文件时测试用
if __name__ == '__main__':

    config = Config()

 
