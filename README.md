# DGLAB-qqbot使用文档

---

杂鱼~就这么喜欢挨电吗~

## 主要功能

使用 qqbot 在qq群中用命令连接 DG-LAB app，修改强度及波形

---

## 1.配置环境

使用python3.10.0
~~~ 
pip install -r requirements.txt 
~~~

## 2.配置qqbot服务

本程序使用qq官方提供的PythonSDK进行编写，文档详见 https://bot.q.qq.com/wiki/

## 3.配置sm.ms服务

需于 https://smms.app （大陆地区推荐）或 https://sm.ms 获取令牌以存储二维码图片

## 4.配置文件

使用前需将 `config.example.yaml` 改名为 `config.yaml`

填入qqbot的appid、secret key及sm.ms的令牌

---

### 开源协议

本代码遵循 Apache-2.0 license 协议，传播时需包含本仓库链接