# -*- mode: python ; coding: utf-8 -*-
# RS-A 桌面版打包规格 (onedir, 控制台窗口 = 日志面板)
# 构建: .venv/Scripts/python.exe -m PyInstaller RS-A.spec --noconfirm
#        --distpath cache/dist --workpath cache/build
# 产物: cache/dist/RS-A/RS-A.exe (整个目录即发行版)
# 本规格为独立仓库形态 (在 RS-agent/ 仓库根执行); 载荷路径相对本文件

a = Analysis(
    ['src/launcher.py'],
    pathex=['.'],
    # 知识库/Wiki/来源: 模块 __file__ 在 frozen 下指向
    # _MEIPASS/src/..., datas 按同路径放置即可被 Path(__file__) 定位
    datas=[
        ('src/knowledge', 'src/knowledge'),
        ('evalset', 'evalset'),
        # dsh 挂接补丁: 品牌替换(DeepSeek→RS-A/去预览版)+rs_* 插件+设置凭证面板
        # 全靠它插入 (漏带 = dsh 裸官方形态, 42 号批次实测翻车)
        ('dsh/cordis.patch.yml', 'RS-agent/dsh'),
    ],
    hiddenimports=[
        # uvicorn 动态加载的标准组件
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        # langgraph 动态导入面
        'langgraph.graph', 'langgraph.prebuilt', 'langgraph.checkpoint',
        # pywebview Windows 后端 (WebView2/WinForms)
        'webview.platforms.edgechromium', 'webview.platforms.winforms',
    ],
    excludes=['tkinter', 'pytest', 'pip'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RS-A',
    debug=False,
    console=False,         # 桌面窗口形态: 无黑色控制台, 日志落 cache/logs
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,             # UPX 压缩易触发杀软误报, 发行包不启用
    name='RS-A',
)
