# -*- coding: utf-8 -*-
import json
import os

import botpy
import qrcode
import requests
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage
from pydglab_ws import StrengthData, Channel, StrengthOperationType, RetCode, DGLabWSServer

from Pulses import PULSE_DATA

test_config = read(os.path.join(os.path.dirname(__file__), "config.yaml"))
ip_addr = test_config['ip_addr']
pic_token = test_config['pic_token']
_log = logging.get_logger()


class UploadImgError(Exception):
    pass


def make_qrcode(data: str):
    img = qrcode.make(data)
    img.save('qrcode.png')


def upload_qrcode():
    """调用sm.ms的api来储存二维码图片"""
    headers = {'Authorization': pic_token}
    files = {'smfile': open("qrcode.png", 'rb')}
    url = 'https://smms.app/api/v2/upload'
    res = requests.post(url, files=files, headers=headers).json()
    if res['code'] == "image_repeated":
        _log.info(f"二维码图片已存在，返回 {res['images']}")
        return res['images']
    elif res['code'] == "success":
        _log.info(f"二维码图片已上传，返回 {res['data']['url']}")
        return res['data']['url']
    else:
        _log.error(f"二维码上传失败\r\n{json.dumps(res, indent=4)}")
        raise UploadImgError


class Commander:
    def __init__(self):
        self.close_tag = False  # 通知协程关闭标志
        self.pulse_close_tag = False
        self.upload_media = None  # 上传至qq服务器的二维码图片，Coroutine对象
        self.size = None  # message.content单词数，类型为int
        self.kwargs = None  # 除command外的参数，类型为str列表
        self.command = None  # message.content中的首个单词
        self.client = None  # DGLabWSServer对象
        self.sever = None  # DGLabWSServer对象
        self.message = None  # message对象
        self.strength = None  # 强度数据，StrengthData对象
        self.status_code = None  # 状态码，int，0为未占用，1为等待连接，2为已连接
        self.current_pulses_A = PULSE_DATA['呼吸']  # 当前波形列表，含默认波形
        self.current_pulses_B = PULSE_DATA['呼吸']

    async def send_message(self, message: str):
        message_result = await self.message._api.post_group_message(
            group_openid=self.message.group_openid,
            msg_type=0,
            msg_id=self.message.id,
            content=message)
        _log.info(f'消息结果 {message_result}')

    async def check_message(self, *args):
        if self.status_code != 2:
            await self.send_message('当前未连接，无法调节参数')
            return False
        # 参数数量检查
        if self.size - 1 != len(args):
            await self.send_message(f'此命令应有{len(args)}个参数')
            _log.warning(f'此命令收到 {self.size - 1} 个参数')
            return False

        for i in range(len(args)):
            if args[i] == str:
                if not isinstance(self.kwargs[i], str):
                    await self.send_message(f"第{i + 1}个参数应为str")
                    _log.warning(f"第{i + 1}个参数应为str，收到 {self.kwargs[i]}")
                    return False
            elif args[i] == int:
                if not isinstance(self.kwargs[i], int):
                    await self.send_message(f"第{i + 1}个参数应为int")
                    _log.warning(f"第{i + 1}个参数应为int，收到 {self.kwargs[i]}")
                    return False
            elif isinstance(args[i], dict) or isinstance(args[i], set):
                if self.kwargs[i] not in args[i]:
                    await self.send_message(f"第{i + 1}个参数名称错误")
                    _log.warning(f"第{i + 1}个参数名称错误，收到 {self.kwargs[i]}")
                    return False
            elif isinstance(args[i], tuple):  # 数值值域检查
                if not self.kwargs[i].isdigit():
                    await self.send_message("强度参数格式错误")
                    _log.warning(f"强度参数格式错误，收到{self.kwargs[i]}")
                    return False
                if not args[i][0] <= int(self.kwargs[i]) <= args[i][1]:
                    await self.send_message('强度参数不在值域内')
                    _log.warning(f'此命令强度参数不在值域内，收到 {self.kwargs[i]}')
                    return False
        return True

    async def reslove(self, message: GroupMessage):
        # 分割命令与内容
        self.kwargs = None
        self.message = message
        self.command = message.content.split()[0]
        self.kwargs = message.content.split()[1:]
        self.size = len(message.content.split())
        # 识别命令并执行
        if self.command == 'increase':
            await self.increase()
        elif self.command == 'decrease':
            await self.decrease()
        elif self.command == 'close':
            await self.close()
        elif self.command == 'connect':
            await self.connect()
        elif self.command == 'set':
            await self.set()
        elif self.command == 'status':
            await self.status()
        elif self.command == 'change':
            await self.change_pulse()
        elif self.command == 'help':
            await self.help()
        else:
            await self.send_message('此命令不存在')
            _log.warning('此命令不存在')

    async def connect(self):
        if self.size >= 2:
            await self.send_message('connect命令不应有参数')
            _log.warning(f"connect命令参数过多：{self.size - 1}")
            return

        if self.status_code == 1:
            await self.message._api.post_group_message(
                group_openid=self.message.group_openid,
                msg_type=7,  # 7表示富媒体类型
                msg_id=self.message.id,
                media=self.upload_media
            )
            _log.info('已重复发送二维码')
            return
        elif self.status_code == 2:
            await self.send_message('当前已连接 app，不可重复连接')
            _log.info('重复连接的请求被拒')
            return

        async with DGLabWSServer("0.0.0.0", 5678, 20) as self.sever:
            self.client = self.sever.new_local_client()
            _log.info(f"已创建DGLabWSServer，产生链接 {self.client.get_qrcode(ip_addr)}")

            # 上传二维码图片至sm.ms服务器
            make_qrcode(self.client.get_qrcode(ip_addr))
            try:
                file_url = upload_qrcode()
            except UploadImgError:
                await self.send_message('上传图片失败')
                return

            # 将图片从sm.ms服务器传至qq服务器
            self.upload_media = await self.message._api.post_group_file(
                group_openid=self.message.group_openid,
                file_type=1,
                url=file_url
            )

            # 图片上传后，会得到Media，用于发送消息
            await self.message._api.post_group_message(
                group_openid=self.message.group_openid,
                msg_type=7,  # 7表示富媒体类型
                msg_id=self.message.id,
                media=self.upload_media
            )
            _log.info("qr码已发送，等待绑定")

            self.status_code = 1
            await self.client.bind()
            self.status_code = 2
            _log.info(f"已与 App {self.client.target_id} 成功绑定")

            # threading.Thread(target=await self._pulses_range()).start()

            # 异步轮询终端状态
            async for data in self.client.data_generator():
                # 接收关闭标志
                if self.close_tag:
                    self.close_tag = False
                    self.status_code = 0
                    _log.info("已主动断开连接")
                    return
                for i in range(5):
                    await self.client.add_pulses(Channel.A, *self.current_pulses_A * 5)
                    await self.client.add_pulses(Channel.B, *self.current_pulses_B * 5)
                # 接收通道强度数据
                if isinstance(data, StrengthData):
                    _log.info(f"从 App 收到通道强度数据更新：{data}")
                    self.strength = data
                # 接收 心跳 / App 断开通知
                elif data == RetCode.CLIENT_DISCONNECTED:
                    self.status_code = 0
                    self.pulse_close_tag = True
                    _log.info("App 端断开连接")
                    return

    async def change_pulse(self):
        if not await self.check_message({'A', 'B'}, PULSE_DATA): return
        if self.kwargs[0] == 'A':
            self.current_pulses_A = PULSE_DATA[self.kwargs[1]]
            await self.send_message("已更改A通道波形，可能出现延迟")
            _log.info(f"已更改A通道波形为{self.kwargs[1]}")
            return
        elif self.kwargs[0] == 'B':
            self.current_pulses_B = PULSE_DATA[self.kwargs[1]]
            await self.send_message("已更改A通道波形，可能出现延迟")
            _log.info(f"已更改B通道波形为{self.kwargs[1]}")
            return

    async def set(self):
        if not await self.check_message({'A', 'B'}, (0, 200)): return

        if self.kwargs[0] == 'A':
            await self.client.set_strength(Channel.A, StrengthOperationType.SET_TO, self.kwargs[1])
            await self.send_message(f'通道A强度已设置至 {self.kwargs[1]}')
            _log.info(f'通道A强度已设置至 {self.kwargs[1]}')
            return
        elif self.kwargs[0] == 'B':
            await self.client.set_strength(Channel.B, StrengthOperationType.SET_TO, self.kwargs[1])
            await self.send_message(f'通道B强度已设置至 {self.kwargs[1]}')
            _log.info(f'通道B强度已设置至 {self.kwargs[1]}')
            return
        _log.error('set命令意外退出')

    async def increase(self):
        if not await self.check_message({'A', 'B'}, (0, 200)): return

        if self.kwargs[0] == 'A':
            await self.client.set_strength(Channel.A, StrengthOperationType.INCREASE, self.kwargs[1])
            await self.send_message(f'通道A强度已增加 {self.kwargs[1]}')
            _log.info(f'通道A强度已增加 {self.kwargs[1]}')
            return
        elif self.kwargs[0] == 'B':
            await self.client.set_strength(Channel.B, StrengthOperationType.INCREASE, self.kwargs[1])
            await self.send_message(f'通道B强度已增加 {self.kwargs[1]}')
            _log.info(f'通道B强度已增加 {self.kwargs[1]}')
            return
        _log.error('increase命令意外退出')

    async def decrease(self):
        if not await self.check_message({'A', 'B'}, (0, 200)): return

        if self.kwargs[0] == 'A':
            await self.client.set_strength(Channel.A, StrengthOperationType.DECREASE, self.kwargs[1])
            await self.send_message(f'通道A强度已降低 {self.kwargs[1]}')
            _log.info(f'通道A强度已降低 {self.kwargs[1]}')
            return
        elif self.kwargs[0] == 'B':
            await self.client.set_strength(Channel.B, StrengthOperationType.DECREASE, self.kwargs[1])
            await self.send_message(f'通道B强度已降低 {self.kwargs[1]}')
            _log.info(f'通道B强度已降低 {self.kwargs[1]}')
            return
        _log.error('decrease命令意外退出')

    # async def add_pulses(self):
    #     if self.size != 3:
    #         await self.send_message('参数数量错误')
    #         _log.warning(f"参数数量错误：{self.size - 1}")
    #         return
    #     elif self.kwargs[0] not in ('A', 'B'):
    #         await self.send_message('通道参数错误')
    #         _log.warning(f'通道参数错误：{self.kwargs[0]}')
    #         return
    #     elif self.kwargs[1] not in PULSE_DATA:
    #         await self.send_message('波形名称不存在')
    #         _log.warning(f"波形名称不存在：{self.kwargs[1]}")
    #         return
    #     if self.kwargs[0] == 'A':
    #         self.current_pulses_A.append(PULSE_DATA[self.kwargs[1]])
    #         await self.send_message(f"{self.kwargs[1]}波形已添加至列表")
    #         _log.info(f"{self.kwargs[1]}波形已添加至列表")

    async def status(self):
        if self.size != 1: await self.send_message('status命令不应有参数')
        if self.status_code == 0:
            await self.send_message('当前未连接')
        elif self.status_code == 1:
            await self.send_message('当前正等待连接')
        elif self.status_code == 2:
            await self.send_message(f'当前已连接\r\nA通道：{self.strength.a} 上限{self.strength.a_limit}\r\n'
                                    f'B通道：{self.strength.b} 上限{self.strength.b_limit}')

    async def close(self):
        self.close_tag = True
        self.pulse_close_tag = True
        _log.info('close_tag已发送')
        await self.send_message("已发送断开连接信号，可能需要较长时间响应")

    async def help(self):
        await self.send_message('这里是命令介绍喵~\r\n'
                                'connect命令用于连接app，无参数，只可同时连接一个客户端\r\n'
                                'set,increase,decrease命令用于设定、增加、减小指定通道的强度，如：set A 100\r\n'
                                'status命令用于查看当前连接状况，强度大小，强度上限，无参数\r\n'
                                'change命令用于更改指定通道波形，如：change A 潮汐，波形名称列表如下：\r\n'
                                '呼吸、潮汐、连击、快速按捏、按捏渐强、心跳节奏、压缩、节奏步伐、颗粒摩擦、渐变弹跳、波浪涟漪、雨水冲刷、变速敲击、信号灯、挑逗1、挑逗2\r\n'
                                'close命令用于关闭连接\r\n\r\n'
                                'tips: 每隔一段时间波形会暂停输出，此时可通过点击app上任意形状按钮继续输出')


commander = Commander()


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 准备就绪！")

    async def on_group_at_message_create(self, message: GroupMessage):
        await commander.reslove(message)


if __name__ == "__main__":
    # 通过预设置的类型，设置需要监听的事件通道
    # intents = botpy.Intents.none()
    # intents.public_messages=True

    # 通过kwargs，设置需要监听的事件通道
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=test_config["appid"], secret=test_config["secret"])
