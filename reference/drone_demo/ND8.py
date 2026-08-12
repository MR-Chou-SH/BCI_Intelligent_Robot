from wheel_core import Decoder
import socket
from scipy import signal
import numpy as np
import threading

class NDThread(threading.Thread,):
    
    def __init__(self, ND_address=('127.0.0.1',8899), srate=250,record_srate=1000):
        
        threading,threading.Thread.__init__(self)
        self.decoder = Decoder()
        self.ND_address = ND_address
        self.list_length = 1500
        self.req_data_bytes=[2, 78, 68, 0, 1, 1, 3, 209, 142, 78, 217]
        self.record_srate = record_srate
        self.srate = srate
        self.eeg_datas = []
        self.filters = self._initFilter()
        self._is_running = True
    
    def _initFilter(self):
        fs = 250  # Sample frequency (Hz)
        f0 = 50.0  # Frequency to be removed from signal (Hz)
        Q = 30.0  # Quality factor

        b, a = signal.iirnotch(f0, Q, fs)
        notch = [b, a]

        b, a = signal.butter(N=5, Wn=90, fs=fs, btype='lowpass')
        bp = [b, a]

        return notch, bp
    
    def connect(self):
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        notconnect = True
        reconnecttime = 0
        while notconnect:
            try:
                self.sock.connect(self.ND_address)
                notconnect = False
                print('Data server connected.')
            except:
                reconnecttime += 1
                if reconnecttime > 5:
                    break
        return notconnect
    
    def disconnect(self):
        
        self._is_running = False
        self.join()
        self.sock.close()
        print('Data server disconnected')
        
        pass
    
    def run(self):
        
        while self._is_running:
            self.sock.send(bytearray(self.req_data_bytes))
            header_bytes = self.sock.recv(7)
            header = self.decoder.decode_bytes(header_bytes)
            payload_length = header['payload_length']
            body = self.sock.recv(payload_length + 5)
            res = self.decoder.decode_bytes(body)
            try:
                if res['timestamp'] == 0:
                    continue
            except Exception as e:
                print("res timestamp error:"+str(e))
            self.eeg_datas.append(res)
            if len(self.eeg_datas) > self.list_length:
                self.eeg_datas.pop(0)
                
    def readFixedData(self, length, stimulation_time):
        
        self.downRatio = int(self.srate*length)
        data = None
        while data is None:
            data = self.read_eeg_data(stimulation_time, length*1000)
        data = self.preprocess(data)
        
        return data
    
    def read_eeg_data(self, start_millis_second, read_millisecond):

        point_per_millis = self.record_srate / 1000
        need_points = int(read_millisecond * point_per_millis)
        eeg_data = None
        enough_data = False
        while len(self.eeg_datas) > 0:
            eeg_left = self.eeg_datas[0]
            if eeg_left is None:
                break
            eeg_packet_start_millis = eeg_left['timestamp']
            res = eeg_left['data']
            packet_time = len(res[0])/self.record_srate * 1000
            if eeg_packet_start_millis + packet_time < start_millis_second:
                self.eeg_datas.pop(0)
                continue
            eeg_start_position = int((start_millis_second - eeg_packet_start_millis) * point_per_millis)
            if eeg_start_position < 0:
                print("eeg time error:{0}".format(eeg_start_position))
                eeg_start_position = 0
            eeg_tmp = []
            eeg_data_length = 0
            for res in self.eeg_datas:
                eeg_packet_data = np.array([[x[0] for x in sublist] for sublist in res['data']])
                eeg_data_length = len(res['data'][0]) + eeg_data_length
                eeg_tmp.append(eeg_packet_data)
                if eeg_data_length - eeg_start_position > need_points:
                    enough_data = True
                    break
            if enough_data:
                eeg_data = np.hstack(eeg_tmp)
                eeg_data = eeg_data[:, eeg_start_position:(eeg_start_position + need_points)]
                print("eeg start points:{0},needPoints:{1},start_millis_second:{2},eeg_packet_millis:{3},eeg_data_shape:{4}".format(
                        eeg_start_position, need_points, start_millis_second, eeg_packet_start_millis, eeg_data.shape))
                break           
        return eeg_data
    
    def preprocess(self, x):
        
        from scipy.signal import resample
        x = resample(x,self.downRatio,axis=-1)

        notchFilter, bpFilter = self.filters
        b_notch, a_notch = notchFilter
        x_notched = signal.filtfilt(b_notch, a_notch, x, axis=-1)
        processed = x_notched

        return processed