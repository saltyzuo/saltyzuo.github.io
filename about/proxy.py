#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简单 HTTP/HTTPS 代理服务器(兼容 Python 2.7+ / 3.x)
支持 CONNECT 隧道，可长时间稳定运行
"""

from __future__ import print_function, unicode_literals
import socket
import select
import threading
import sys
import time

# 兼容 URL 解析模块
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

# 配置
BUFFER_SIZE = 8192          # 缓冲区大小
TIMEOUT = 60                # 连接超时（秒）
LISTEN_PORT = 8080          # 代理监听端口（可修改）

class ProxyHandler(threading.Thread):
    """处理单个客户端连接"""
    def __init__(self, client_sock, client_addr):
        threading.Thread.__init__(self)
        self.daemon = True
        self.client_sock = client_sock
        self.client_addr = client_addr
        self.target_sock = None

    def _forward_data(self, sock_from, sock_to):
        """单向转发数据（非阻塞）"""
        try:
            data = sock_from.recv(BUFFER_SIZE)
            if not data:
                return False
            sock_to.sendall(data)
            return True
        except (socket.error, IOError):  # 兼容 Python 2/3
            return False

    def _tunnel(self):
        """建立双向转发隧道（用于 CONNECT 方法）"""
        try:
            sockets = [self.client_sock, self.target_sock]
            while True:
                rlist, _, _ = select.select(sockets, [], [], TIMEOUT)
                if not rlist:
                    break
                for sock in rlist:
                    other = self.target_sock if sock is self.client_sock else self.client_sock
                    if not self._forward_data(sock, other):
                        return
        except Exception:
            pass
        finally:
            self._close()

    def _handle_http(self, method, url, headers):
        """处理普通 HTTP 代理请求（非 CONNECT）"""
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query

        try:
            self.target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.target_sock.settimeout(TIMEOUT)
            self.target_sock.connect((host, port))
        except Exception as e:
            print("[{}] 连接目标失败 {}:{} - {}".format(self.client_addr, host, port, e))
            self._send_error(502, b"Bad Gateway")
            self._close()
            return

        request_line = "{} {} HTTP/1.1\r\n".format(method, path)
        new_headers = []
        host_header_found = False
        for key, value in headers:
            if key.lower() == 'host':
                new_headers.append("{}: {}:{}".format(key, host, port))
                host_header_found = True
            else:
                new_headers.append("{}: {}".format(key, value))
        if not host_header_found:
            new_headers.append("Host: {}:{}".format(host, port))
        header_block = request_line + "\r\n".join(new_headers) + "\r\n\r\n"

        try:
            self.target_sock.sendall(header_block.encode())
            self._tunnel()
        except Exception as e:
            print("[{}] 转发数据错误 - {}".format(self.client_addr, e))
            self._close()

    def _send_error(self, code, message):
        """发送简单的错误响应"""
        response = "HTTP/1.1 {} {}\r\nContent-Length: 0\r\n\r\n".format(code, message.decode())
        try:
            self.client_sock.sendall(response.encode())
        except:
            pass

    def _parse_request(self, data):
        """解析请求行和头部"""
        try:
            # 数据是 bytes，需要解码
            if isinstance(data, bytes):
                data = data.decode('latin-1')
            lines = data.split('\r\n')
            request_line = lines[0]
            method, url, version = request_line.split(' ', 2)
            headers = []
            for line in lines[1:]:
                if not line:
                    break
                key, value = line.split(':', 1)
                headers.append((key.strip(), value.strip()))
            return method, url, headers
        except Exception:
            return None, None, None

    def run(self):
        """主处理逻辑"""
        try:
            data = self.client_sock.recv(BUFFER_SIZE)
            if not data:
                return

            method, url, headers = self._parse_request(data)
            if not method:
                self._send_error(400, b"Bad Request")
                return

            if method.upper() == 'CONNECT':
                host_port = url.split(':')
                host = host_port[0]
                port = int(host_port[1]) if len(host_port) > 1 else 443
                try:
                    self.target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.target_sock.settimeout(TIMEOUT)
                    self.target_sock.connect((host, port))
                    self.client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    self._tunnel()
                except Exception as e:
                    print("[{}] CONNECT 失败 {}:{} - {}".format(self.client_addr, host, port, e))
                    self._send_error(502, b"Bad Gateway")
                return

            self._handle_http(method, url, headers)

        except socket.timeout:
            print("[{}] 连接超时".format(self.client_addr))
        except Exception as e:
            print("[{}] 处理异常 - {}".format(self.client_addr, e))
        finally:
            self._close()

    def _close(self):
        """关闭所有连接"""
        for sock in (self.client_sock, self.target_sock):
            if sock:
                try:
                    sock.close()
                except:
                    pass

class ProxyServer:
    """代理服务器主控"""
    def __init__(self, port=LISTEN_PORT):
        self.port = port
        self.server_sock = None
        self.running = False

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(('0.0.0.0', self.port))
        self.server_sock.listen(100)
        self.running = True
        print("代理服务器启动，监听端口 {}".format(self.port))
        print("使用方法：将浏览器或系统的代理设置为 HTTP 代理，地址 127.0.0.1:{}".format(self.port))
        print("按 Ctrl+C 停止服务器")

        while self.running:
            try:
                client_sock, client_addr = self.server_sock.accept()
                client_sock.settimeout(TIMEOUT)
                handler = ProxyHandler(client_sock, client_addr)
                handler.start()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print("接受连接时出错: {}".format(e))
                continue

        self.stop()

    def stop(self):
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        print("代理服务器已停止")

def main():
    port = LISTEN_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            print("用法: {} [端口号]".format(sys.argv[0]))
            sys.exit(1)
    server = ProxyServer(port)
    server.start()

if __name__ == "__main__":
    main()