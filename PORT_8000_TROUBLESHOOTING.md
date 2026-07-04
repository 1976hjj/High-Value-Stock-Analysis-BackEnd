# 关闭占用 8000 端口的后端进程

查看是谁占用了 `127.0.0.1:8000`：

```powershell
netstat -ano | findstr 127.0.0.1:8000
```

如果看到类似下面的输出：

```text
TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    34492
```

最后一列 `34492` 就是进程 PID。关闭它：

```powershell
Stop-Process -Id 34492 -Force
```

如果后端是在当前窗口启动的，也可以直接按 `Ctrl + C` 停止。
