import struct
import time



class Constant:
    stx = 0x02
    etx = 0x03
    # wheel server: magic number
    magic_0 = 0x4e
    magic_1 = 0x44

queue_length = 10


class NeuroDanceQueue:

    def __init__(self):
        self.buffer = [None] * queue_length
        self.buf_length = queue_length
        self.front = queue_length - 1
        self.rear = queue_length - 1

    def set_null_queue(self):
        self.front = queue_length - 1
        self.rear = queue_length - 1

    def empty_queue(self):
        if self.rear == self.front:
            # 空的 返回1
            return 1
        else:
            return 0

    # 读头数据
    def front_queue(self):
        if self.empty_queue():
            return 0
        else:
            return self.buffer[(self.front + 1) % queue_length]

    # 数据入队函数 （写）#
    def en_queue(self, word):
        re = (self.rear + 1) % queue_length
        if re == self.front:
            return 0
        else:
            self.rear = re
            self.buffer[self.rear] = word
            return 1

    # 出队函数
    def de_queue(self):
        if self.empty_queue() == 1:
            return 0
        else:
            self.front = (self.front + 1) % queue_length
            tmp = self.buffer[self.front]
            return tmp

    def queue_status(self):
        if self.empty_queue():
            # 空的
            return 0
        elif (self.rear + 1) % queue_length == self.front:
            # 满的
            return 2
        else:
            # 有数据，但没满
            return 1


class Crc32:
    __crc32_tab = [
        0x00000000, 0x04C11DB7, 0x09823B6E, 0x0D4326D9, 0x130476DC, 0x17C56B6B,
        0x1A864DB2, 0x1E475005, 0x2608EDB8, 0x22C9F00F, 0x2F8AD6D6, 0x2B4BCB61,
        0x350C9B64, 0x31CD86D3, 0x3C8EA00A, 0x384FBDBD, 0x4C11DB70, 0x48D0C6C7,
        0x4593E01E, 0x4152FDA9, 0x5F15ADAC, 0x5BD4B01B, 0x569796C2, 0x52568B75,
        0x6A1936C8, 0x6ED82B7F, 0x639B0DA6, 0x675A1011, 0x791D4014, 0x7DDC5DA3,
        0x709F7B7A, 0x745E66CD, 0x9823B6E0, 0x9CE2AB57, 0x91A18D8E, 0x95609039,
        0x8B27C03C, 0x8FE6DD8B, 0x82A5FB52, 0x8664E6E5, 0xBE2B5B58, 0xBAEA46EF,
        0xB7A96036, 0xB3687D81, 0xAD2F2D84, 0xA9EE3033, 0xA4AD16EA, 0xA06C0B5D,
        0xD4326D90, 0xD0F37027, 0xDDB056FE, 0xD9714B49, 0xC7361B4C, 0xC3F706FB,
        0xCEB42022, 0xCA753D95, 0xF23A8028, 0xF6FB9D9F, 0xFBB8BB46, 0xFF79A6F1,
        0xE13EF6F4, 0xE5FFEB43, 0xE8BCCD9A, 0xEC7DD02D, 0x34867077, 0x30476DC0,
        0x3D044B19, 0x39C556AE, 0x278206AB, 0x23431B1C, 0x2E003DC5, 0x2AC12072,
        0x128E9DCF, 0x164F8078, 0x1B0CA6A1, 0x1FCDBB16, 0x018AEB13, 0x054BF6A4,
        0x0808D07D, 0x0CC9CDCA, 0x7897AB07, 0x7C56B6B0, 0x71159069, 0x75D48DDE,
        0x6B93DDDB, 0x6F52C06C, 0x6211E6B5, 0x66D0FB02, 0x5E9F46BF, 0x5A5E5B08,
        0x571D7DD1, 0x53DC6066, 0x4D9B3063, 0x495A2DD4, 0x44190B0D, 0x40D816BA,
        0xACA5C697, 0xA864DB20, 0xA527FDF9, 0xA1E6E04E, 0xBFA1B04B, 0xBB60ADFC,
        0xB6238B25, 0xB2E29692, 0x8AAD2B2F, 0x8E6C3698, 0x832F1041, 0x87EE0DF6,
        0x99A95DF3, 0x9D684044, 0x902B669D, 0x94EA7B2A, 0xE0B41DE7, 0xE4750050,
        0xE9362689, 0xEDF73B3E, 0xF3B06B3B, 0xF771768C, 0xFA325055, 0xFEF34DE2,
        0xC6BCF05F, 0xC27DEDE8, 0xCF3ECB31, 0xCBFFD686, 0xD5B88683, 0xD1799B34,
        0xDC3ABDED, 0xD8FBA05A, 0x690CE0EE, 0x6DCDFD59, 0x608EDB80, 0x644FC637,
        0x7A089632, 0x7EC98B85, 0x738AAD5C, 0x774BB0EB, 0x4F040D56, 0x4BC510E1,
        0x46863638, 0x42472B8F, 0x5C007B8A, 0x58C1663D, 0x558240E4, 0x51435D53,
        0x251D3B9E, 0x21DC2629, 0x2C9F00F0, 0x285E1D47, 0x36194D42, 0x32D850F5,
        0x3F9B762C, 0x3B5A6B9B, 0x0315D626, 0x07D4CB91, 0x0A97ED48, 0x0E56F0FF,
        0x1011A0FA, 0x14D0BD4D, 0x19939B94, 0x1D528623, 0xF12F560E, 0xF5EE4BB9,
        0xF8AD6D60, 0xFC6C70D7, 0xE22B20D2, 0xE6EA3D65, 0xEBA91BBC, 0xEF68060B,
        0xD727BBB6, 0xD3E6A601, 0xDEA580D8, 0xDA649D6F, 0xC423CD6A, 0xC0E2D0DD,
        0xCDA1F604, 0xC960EBB3, 0xBD3E8D7E, 0xB9FF90C9, 0xB4BCB610, 0xB07DABA7,
        0xAE3AFBA2, 0xAAFBE615, 0xA7B8C0CC, 0xA379DD7B, 0x9B3660C6, 0x9FF77D71,
        0x92B45BA8, 0x9675461F, 0x8832161A, 0x8CF30BAD, 0x81B02D74, 0x857130C3,
        0x5D8A9099, 0x594B8D2E, 0x5408ABF7, 0x50C9B640, 0x4E8EE645, 0x4A4FFBF2,
        0x470CDD2B, 0x43CDC09C, 0x7B827D21, 0x7F436096, 0x7200464F, 0x76C15BF8,
        0x68860BFD, 0x6C47164A, 0x61043093, 0x65C52D24, 0x119B4BE9, 0x155A565E,
        0x18197087, 0x1CD86D30, 0x029F3D35, 0x065E2082, 0x0B1D065B, 0x0FDC1BEC,
        0x3793A651, 0x3352BBE6, 0x3E119D3F, 0x3AD08088, 0x2497D08D, 0x2056CD3A,
        0x2D15EBE3, 0x29D4F654, 0xC5A92679, 0xC1683BCE, 0xCC2B1D17, 0xC8EA00A0,
        0xD6AD50A5, 0xD26C4D12, 0xDF2F6BCB, 0xDBEE767C, 0xE3A1CBC1, 0xE760D676,
        0xEA23F0AF, 0xEEE2ED18, 0xF0A5BD1D, 0xF464A0AA, 0xF9278673, 0xFDE69BC4,
        0x89B8FD09, 0x8D79E0BE, 0x803AC667, 0x84FBDBD0, 0x9ABC8BD5, 0x9E7D9662,
        0x933EB0BB, 0x97FFAD0C, 0xAFB010B1, 0xAB710D06, 0xA6322BDF, 0xA2F33668,
        0xBCB4666D, 0xB8757BDA, 0xB5365D03, 0xB1F740B4
    ]

    @classmethod
    def crc32(cls, data):
        seed = 0xFFFFFFFF
        final = 0
        nReg = seed
        length = len(data) // 4
        for i in range(0, length):
            buffer = b''.join(d.to_bytes(1, byteorder='big') for d in data[i * 4:i * 4 + 4])
            aaa = int.from_bytes(buffer, byteorder='little')
            nReg ^= aaa
            for j in range(4):
                index = (nReg >> 24) & 0xFF

                nTemp = Crc32.__crc32_tab[index]
                nReg = (nReg << 8) & 0xffffffff
                nReg ^= nTemp
        return nReg ^ final


class Encoder:

    constant = Constant

    def req_data(self):
        datas = []
        datas.append(Constant.stx)
        # destinationId 0x80
        datas.append(Constant.magic_0)
        # sourceId 0x00
        datas.append(Constant.magic_1)
        # pack_length
        payload = [0x01]
        pack_length = len(payload)
        pack_length_bytes = pack_length.to_bytes(length=2, byteorder="little")
        datas.append(pack_length_bytes[1])
        datas.append(pack_length_bytes[0])
        for i in range(0, len(payload)):
            datas.append(payload[i])
        datas.append(Constant.etx)
        remainder = len(datas) % 4
        if remainder != 0:
            for j in range(5 - remainder):
                datas.append(0x00)
        crc32 = Crc32.crc32(datas)
        if remainder != 0:
            del datas[len(datas) - 5 + remainder:]
        crc_bytes = crc32.to_bytes(length=4, byteorder="little")
        datas.append(crc_bytes[3])
        datas.append(crc_bytes[2])
        datas.append(crc_bytes[1])
        datas.append(crc_bytes[0])
        print(datas)
        datas = bytes(datas)
        return datas


buf_length = 1000000


class Decoder:
    __step = 0
    # 保存所有字节的数组
    __dat_buf = [None] * buf_length
    # 当前字节所在的索引
    __index = 0
    # cmd + pm + payload的长度，即payload.length + 2
    __len = 0
    __payload_len_0 = 0
    __payload_len_1 = 0
    __payload_len_2 = 0
    __payload_len_3 = 0
    payload_length = 0
    # 用来记录已读取的payload的字计数
    __len_c = 0
    # 当前字节
    __dat = 0x00
    # 目的设备ID，指令要传达的对象
    __destination = 0x00
    # 源设备ID，指令发出的对象，如：0x00（APP）
    __source = None
    # 消息体
    __payload = [None] * (buf_length - 7)
    __crc32 = 0
    # 指令代码
    __cmd = 0x00
    # 指令代码参数
    __pm = 0x00
    # 指令响应  0x4e：设备响应失败   0x50:响应成功
    __pn = None

    def decode_bytes(self, bytes):
        index = 0
        res = {}
        while index < len(bytes):
            try:
                self.__dat = int(bytes[index])
                index = index + 1
                if self.__step == 0:
                    if self.__dat == Constant.stx:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 1
                    else:
                        self.clear()
                elif self.__step == 1:
                    if self.__dat == Constant.magic_0:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__destination = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 2
                    else:
                        self.clear()
                elif self.__step == 2:
                    if self.__dat == Constant.magic_1:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__source = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 3
                    else:
                        self.clear()
                elif self.__step == 3:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__len = self.__dat
                    self.__payload_len_0 = self.__dat
                    self.__index = self.__index + 1
                    self.__step = 4
                elif self.__step == 4:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_1 = self.__dat
                    self.payload_length = self.__payload_len_0 * 256 * 256 * 256 + self.__payload_len_1 * 256 * 256
                    self.__index = self.__index + 1
                    self.__step = 5
                elif self.__step == 5:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_2 = self.__dat
                    self.payload_length = self.payload_length + self.__payload_len_2 * 256
                    self.__index = self.__index + 1
                    self.__step = 6
                elif self.__step == 6:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_3 = self.__dat
                    self.payload_length = self.payload_length + self.__payload_len_3
                    self.__index = self.__index + 1
                    res['payload_length'] = self.payload_length
                    self.__step = 7
                elif self.__step == 7:
                    if self.payload_length == 0:
                        self.clear()
                        break
                    if self.__len_c < self.payload_length:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__len_c = self.__len_c + 1
                        self.__index = self.__index + 1
                        if self.__len_c == self.payload_length:
                            self.__payload = self.__dat_buf[7: self.__index]
                            self.__step = 8
                elif self.__step == 8:
                    if self.__dat == Constant.etx:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 9
                    else:
                        self.clear()
                elif self.__step == 9:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__crc32 = self.__dat * 256 * 256 * 256
                    self.__index = self.__index + 1
                    self.__step = 10
                elif self.__step == 10:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__index = self.__index + 1
                    self.__crc32 = self.__crc32 + self.__dat * 256 * 256
                    self.__step = 11
                elif self.__step == 11:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__index = self.__index + 1
                    self.__crc32 = self.__crc32 + self.__dat * 256
                    self.__step = 12
                elif self.__step == 12:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__crc32 = self.__crc32 + self.__dat
                    self.__step = 13
                    crc32_bytes = self.__dat_buf[0:self.__index - 3]
                    # 不足4位补0
                    remainder = len(crc32_bytes) % 4
                    if remainder != 0:
                        for j in range(5 - remainder):
                            crc32_bytes.append(0x00)
                    crc32_cal = Crc32.crc32(crc32_bytes)
                    if crc32_cal == self.__crc32:
                        # byte_str = ' '.join(['%02X' % i for i in self.__dat_buf[0:self.__index + 1]])
                        if self.__len == 2:
                            self.clear()
                            break
                        if len(self.__payload) == 1:
                            self.clear()
                            break
                        payload_index = 0
                        # model目前是写死为EEG,后面扩展
                        model = self.__payload[payload_index:payload_index + 2]
                        payload_index = 2 + payload_index
                        timestamp_bytes = self.__payload[payload_index:payload_index + 8]
                        timestamp = int.from_bytes(timestamp_bytes, byteorder='big', signed=True)
                        payload_index = 8 + payload_index
                        channel_count_bytes = self.__payload[payload_index:payload_index + 2]
                        channel_count = int.from_bytes(channel_count_bytes, byteorder='big', signed=True)
                        payload_index = 2 + payload_index
                        point_count_per_channel_bytes = self.__payload[payload_index:payload_index + 2]
                        point_count_per_channel = int.from_bytes(point_count_per_channel_bytes, byteorder='big',
                                                                 signed=True)
                        payload_index = 2 + payload_index
                        # print("timestamp:{0}".format(timestamp))
                        # print("channel_count:{0},point_count_per_channel:{1}".format(channel_count,
                        #                                                              point_count_per_channel))
                        datas = []
                        for i in range(channel_count):
                            ch_bytes = self.__payload[payload_index: payload_index + point_count_per_channel * 4]
                            ch_data = self.points_by_4bytes(ch_bytes)
                            datas.append(ch_data)
                            payload_index = payload_index + point_count_per_channel * 4
                        res['timestamp'] = timestamp
                        res['data'] = datas
                        res['model'] = model
                    else:
                        print("crc error")
                    self.clear()
                else:
                    self.clear()
            except Exception as e:
                self.clear()
                print("error:{0}".format(e))
                return res
        return res

    def decode(self, queue):
        res = {}
        while queue.empty_queue() != 1:
            try:
                self.__dat = int(queue.de_queue())
                if self.__step == 0:
                    if self.__dat == Constant.stx:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 1
                    else:
                        self.clear()
                elif self.__step == 1:
                    if self.__dat == Constant.magic_0:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__destination = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 2
                    else:
                        self.clear()
                elif self.__step == 2:
                    if self.__dat == Constant.magic_1:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__source = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 3
                    else:
                        self.clear()
                elif self.__step == 3:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__len = self.__dat
                    self.__payload_len_0 = self.__dat
                    self.__index = self.__index + 1
                    self.__step = 4
                elif self.__step == 4:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_1 = self.__dat
                    self.payload_length = self.__payload_len_0 * 256 * 256 * 256 + self.__payload_len_1 * 256 * 256
                    self.__index = self.__index + 1
                    self.__step = 5
                elif self.__step == 5:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_2 = self.__dat
                    self.payload_length = self.payload_length + self.__payload_len_2 * 256
                    self.__index = self.__index + 1
                    self.__step = 6
                elif self.__step == 6:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__payload_len_3 = self.__dat
                    self.payload_length = self.payload_length + self.__payload_len_3
                    self.__index = self.__index + 1
                    res['payload_length'] = self.payload_length
                    self.__step = 7
                elif self.__step == 7:
                    if self.payload_length == 0:
                        self.clear()
                        break
                    if self.__len_c < self.payload_length:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__len_c = self.__len_c + 1
                        self.__index = self.__index + 1
                        if self.__len_c == self.payload_length:
                            self.__payload = self.__dat_buf[7: self.__index]
                            self.__step = 8
                elif self.__step == 8:
                    if self.__dat == Constant.etx:
                        self.__dat_buf[self.__index] = self.__dat
                        self.__index = self.__index + 1
                        self.__step = 9
                    else:
                        self.clear()
                elif self.__step == 9:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__crc32 = self.__dat * 256 * 256 * 256
                    self.__index = self.__index + 1
                    self.__step = 10
                elif self.__step == 10:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__index = self.__index + 1
                    self.__crc32 = self.__crc32 + self.__dat * 256 * 256
                    self.__step = 11
                elif self.__step == 11:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__index = self.__index + 1
                    self.__crc32 = self.__crc32 + self.__dat * 256
                    self.__step = 12
                elif self.__step == 12:
                    self.__dat_buf[self.__index] = self.__dat
                    self.__crc32 = self.__crc32 + self.__dat
                    self.__step = 13
                    crc32_bytes = self.__dat_buf[0:self.__index - 3]
                    # 不足4位补0
                    remainder = len(crc32_bytes) % 4
                    if remainder != 0:
                        for j in range(5 - remainder):
                            crc32_bytes.append(0x00)
                    crc32_cal = Crc32.crc32(crc32_bytes)
                    if crc32_cal == self.__crc32:
                        # byte_str = ' '.join(['%02X' % i for i in self.__dat_buf[0:self.__index + 1]])
                        if self.__len == 2:
                            self.clear()
                            break
                        if len(self.__payload) == 1:
                            self.clear()
                            break
                        payload_index = 0
                        # model目前是写死为EEG,后面扩展
                        model = self.__payload[payload_index:payload_index+2]
                        payload_index = 2 + payload_index
                        timestamp_bytes = self.__payload[payload_index:payload_index+8]
                        timestamp = int.from_bytes(timestamp_bytes, byteorder='big', signed=True)
                        payload_index = 8 + payload_index
                        channel_count_bytes = self.__payload[payload_index:payload_index+2]
                        channel_count = int.from_bytes(channel_count_bytes, byteorder='big', signed=True)
                        payload_index = 2 + payload_index
                        point_count_per_channel_bytes = self.__payload[payload_index:payload_index+2]
                        point_count_per_channel = int.from_bytes(point_count_per_channel_bytes, byteorder='big', signed=True)
                        payload_index = 2 + payload_index
                        # print("timestamp:{0}".format(timestamp))
                        print("channel_count:{0},point_count_per_channel:{1}".format(channel_count, point_count_per_channel))
                        datas = []
                        for i in range(channel_count):
                            ch_bytes = self.__payload[payload_index: payload_index + point_count_per_channel * 4]
                            ch_data = self.points_by_4bytes(ch_bytes)
                            datas.append(ch_data)
                            payload_index = payload_index + point_count_per_channel * 4
                        res['timestamp'] = timestamp
                        res['data'] = datas
                        res['model'] = model
                    else:
                        print("crc error")
                    self.clear()
                else:
                    self.clear()
            except Exception as e:
                self.clear()
                print("error:{0}".format(e))
        return res

    def points_by_4bytes(self, datas):
        length = len(datas)
        points = []
        for i in range(0, length, 4):
            value = struct.unpack('>f', bytearray(datas[i:i + 4]))
            # value = int.from_bytes(datas[i:i + 4], byteorder='big', signed=True)
            points.append(value)
        return points

    def clear(self):
        self.__step = 0
        self.__dat_buf = [None] * buf_length
        self.__index = 0
        self.__len = 0
        self.__payload_len_0 = 0
        self.__payload_len_1 = 0
        self.__payload_len_2 = 0
        self.__payload_len_3 = 0
        self.payload_length = 0
        self.__len_c = 0
        self.__dat = None
        self.__crc32 = 0
        self.__payload = [None] * (buf_length - 7)
        self.__source = None
        self.__destination = None
        self.__cmd = None
        self.__pm = None
        self.__pn = None
