import math
import time

from neuro_dance.nd_device_process import NdDeviceBase
from neuro_dance.linkedlist import NdLinkedlist
import numpy as np

# one second has 5 package
package_per_second = 5
# 5 minute data
eeg_package_count = package_per_second * 60 * 5
eog_package_count = package_per_second * 60 * 5


class NdDevice(NdDeviceBase):    #继承父类 NdDeviceBase 是SDK提供的基类，包含了所有通信逻辑
    # NdLinkedlist 是一个链表结构，用来缓存数据
    eeg_datas = NdLinkedlist()
    eog_datas = NdLinkedlist()
    eeg_sample = 1000    #默认采样率
    mode = None   #模式（serial  tcp）

    def __init__(self, mode, com, tcp_ip, tcp_port, host_mac_bytes=None):
        #super(NdDeviceBase, self).__init__(mode, com, tcp_ip, tcp_port, host_mac_bytes)
        NdDeviceBase.__init__(self, mode, com, tcp_ip, tcp_port, host_mac_bytes)
        self.mode = mode

 #一次性查询dongle（接收器） 的三个信息：版本号、SN序列号、MAC地址
    def host_info(self):
        super(NdDevice, self).host_version_info()
        time.sleep(0.01)
        super(NdDevice, self).host_sn_info()
        time.sleep(0.01)
        super(NdDevice, self).host_mac_info()

#一次性查询头戴设备的三个信息
    def device_info(self): 
        super(NdDevice, self).device_version_info()
        time.sleep(0.01)
        super(NdDevice, self).device_sn_info()
        time.sleep(0.01)
        super(NdDevice, self).device_mac_info()

#查询头戴设备的电量
    def battery(self):
        super(NdDevice, self).device_battery()

#让dongle和头戴设备进行蓝牙配对
    def device_pair(self, mac):
        super(NdDevice, self).pair(mac)

    # scan ble device
    # received ble device from devices_received(devices)
    def devices_scan(self):   #扫描附近的蓝牙设备
        super(NdDevice, self).device_scan()


#回调函数：显示已经接收到了自己主动查询的信息，和_info配对使用
    def host_mac_received(self, host_mac):
        print("Test host_mac_received:"+host_mac)

    def host_sn_received(self, host_sn):
        print("Test host_sn_received:"+host_sn)

    def channel_received(self, data):
        print("Test channel_received:"+data)

    def host_version_received(self, host_version):
        print("Test host_version_received:"+host_version)

    def device_mac_received(self, device_mac):
        print("Test device mac:{0}".format(device_mac))

    def device_sn_received(self, device_sn):
        print("Test device_sn_received:{0}".format(device_sn))

    def device_battery_received(self, battery):
        print("Test battery:{0}".format(battery))

    def device_version_received(self, device_version):
        print("Test device version:{0}".format(device_version))

#回调函数
    def eeg_received(self, data): #接收EEG数据的回调
        shape = self.array_shape(data['data'])
        print("unix milliseconds(first point time):{0},shape:{1},data:{2}".format(data['timestamp'], shape, data['data']))
        if self.eeg_datas.length() > eeg_package_count:  
            self.eeg_datas.removeHead()
        self.eeg_datas.add(data)      #把新收到的数据包添加到链表尾部
#eeg_datas 是一个链表，存储历史数据
# eeg_package_count 是最大存储量（5分钟的数据包数量）
# 如果存储的数据包超过了限制，就删除最旧的那个（removeHead 删除头部）
# 这叫先进先出队列，保证内存不会无限增长




    def eog_received(self, data):
        shape = self.array_shape(data['data'])
        # print("unix milliseconds(first point time):{0},shape:{1},data:{2}".format(data['timestamp'], shape, data['data']))
        if self.eog_datas.length() > eog_package_count:
            self.eog_datas.removeHead()
        self.eog_datas.add(data)


#TCP模式读取历史数据
    def __read_eeg_date_tcp(self, start_millis_second, read_millisecond, freq):
        point_per_millis = freq / 1000
        eeg_data = None
        while self.eeg_datas.length() > 0:
            eeg_left = self.eeg_datas.eleAt(0)
            if eeg_left is None:
                break
            packet_start_millis = eeg_left['timestamp']
            packet_end_millis = len(eeg_left['data'][0]) * 1000 / freq + packet_start_millis
            if packet_end_millis < start_millis_second:
                self.eeg_datas.removeHead()
                continue
            # 判断数据长度是否够
            eeg_start_position = int((start_millis_second - packet_start_millis) * point_per_millis)
            if eeg_start_position < 0:
                eeg_start_position = 0
            # 多加10个冗余点
            need_point_count = int(read_millisecond * point_per_millis)
            check_point_count = need_point_count + eeg_start_position + 10
            enough, channel_data = self.__check_data_enough(self.eeg_datas, check_point_count)
            if enough:
                eeg_data = np.hstack(channel_data)
                eeg_data = eeg_data[:, eeg_start_position:(eeg_start_position + need_point_count)]
                print(
                    "eeg start points:{0},needPoints:{1},start_millis_second:{2},eeg_packet_millis:{3},eeg_data_shape:{4}".format(
                        eeg_start_position, need_point_count, start_millis_second, packet_start_millis,
                        eeg_data.shape))
                break
        return eeg_data

#检查数据是否足够
    def __check_data_enough(self, datas, read_point_count):
        eleIndex = 0
        count = 0
        need_data = []
        while datas.length() > eleIndex and count < read_point_count:
            ele = datas.eleAt(eleIndex)
            if ele is None:
                break
            count = count + len(ele['data'][0])
            eleIndex = eleIndex + 1
            need_data.append(ele['data'])
        return count >= read_point_count, need_data

    def read_eeg_data(self, start_millis_second, read_millisecond, freq = 250):
        """
        :param start_millis_second:
        :param read_millisecond:  read_millisecond,not end_millis_second !!!!
        :return:
        """

        if self.mode == 'serial':
            return self.__read_eeg_from_serial(start_millis_second, read_millisecond, freq)
        elif self.mode == 'tcp':
            return self.__read_eeg_date_tcp(start_millis_second, read_millisecond, freq)


    def __read_eeg_from_serial(self, start_millis_second, read_millisecond, freq = 1000):
        packet_size = freq / package_per_second
        packet_time = 200
        point_per_millis = packet_size / packet_time
        # 以防万一，多取两个包，保证截取时足够长
        packet_num = math.ceil(read_millisecond * point_per_millis / packet_size) + 2
        eeg_data = None
        while self.eeg_datas.length() > 0:
            eeg_left = self.eeg_datas.eleAt(0)
            if eeg_left is None:
                break
            eeg_packet_start_millis = eeg_left['timestamp']
            if eeg_packet_start_millis + packet_time < start_millis_second:
                self.eeg_datas.removeHead()
                continue
            if self.eeg_datas.length() > packet_num:
                # 掐头去尾
                eeg_start_position = int((start_millis_second - eeg_packet_start_millis) * point_per_millis)
                if eeg_start_position < 0:
                    print("eeg time error:{0}".format(eeg_start_position))
                    eeg_start_position = 0
                need_points = int(read_millisecond * point_per_millis)
                eeg_tmp = []
                for i in range(packet_num):
                    chan = self.eeg_datas.eleAt(i)
                    eeg_tmp.append(chan['data'])
                eeg_data = np.hstack(eeg_tmp)
                eeg_data = eeg_data[:, eeg_start_position:(eeg_start_position + need_points)]
                print(
                    "eeg start points:{0},needPoints:{1},start_millis_second:{2},eeg_packet_millis:{3},eeg_data_shape:{4}".format(
                        eeg_start_position, need_points, start_millis_second, eeg_packet_start_millis,
                        eeg_data.shape))
                break
        return eeg_data

    def devices_received(self, devices):
        print("devices count:{0}".format(len(devices)))
        for device in devices:
            print("name:{0},mac:{1},rssi:{2}".format(device['name'], device['mac'], device['rssi']))

    def cmd_error(self, cmd, pm, code):
        print("cmd error:{0},pm:{1},code:{2}".format(cmd, pm, code))

    def crc_error(self):
        print("crc error")

    def array_shape(self, arr):
        if isinstance(arr, list):
            return [len(arr)] + self.array_shape(arr[0])
        else:
            return []

def tcp_test():
    nd_device = NdDevice(mode='tcp', com='', tcp_ip='192.168.10.13', tcp_port=8899, host_mac_bytes=None)
    nd_device.start()
    index = 0
    while index < 10000:
        time.sleep(0.1)

        index = index + 1
        millis_second = int(round(time.time() * 1000))
        time_span = 1000
        read_data = nd_device.read_eeg_data(millis_second - time_span, time_span)
        # if read_data is not None:
            # print(read_data)

    nd_device.close()

def serial_test():
    nd_device = NdDevice(mode='serial', com='com3', tcp_ip='', tcp_port=None, host_mac_bytes=None)
    nd_device.start()
    nd_device.host_mac_info()
    nd_device.host_version_info()

    #nd_device.device_info()
    # nd_device.host_device_connect()
    nd_device.battery()
    nd_device.eeg_channel_config(250)
    nd_device.eeg_channel_enable()
    # # nd_device.eeg_disable()
    # nd_device.eeg_channel_config(1000)
    # nd_device.eeg_channel_enable()
    index = 0
    while index < 10000:
        time.sleep(3)
        # nd_device.host_mac_info()
        # nd_device.host_version_info()
        #nd_device.battery()
        # nd_device.host_info()
        # nd_device.host_sn_info()
        index = index + 1
        millis_second = int(round(time.time() * 1000))
        time_span = 1000
        read_data = nd_device.read_eeg_data(millis_second - time_span, time_span)
        if read_data is not None:
            print(read_data)
        #     print("Device Connect:{0}".format(nd_device.is_device_connected()))
    nd_device.eeg_disable()
    nd_device.eog_disable()
    nd_device.close()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/


if __name__ == '__main__':
    serial_test()