# ROCO附近精灵

Windows 桌面精灵信息与探索路线工具，作者：**芝士奶龙**。

支持本地 Npcap 采集和 PCAP 离线回放，提供竖版悬浮雷达、筛选、战斗信息和按世界存档。

## 功能

- 附近精灵：名称、声音、身高、体重、距离与八方向；支持声音和极端身高筛选。
- 战斗信息：独立显示当前敌方，支持已验证的转换消息；性格标注“只适用于S5赛季”。
- 悬浮雷达：候选、收藏、战斗列表，支持缩放与地图模式。
- 世界存档：按世界保存稀有记录和探索路线，支持文件导入导出与清理。
- 默认输出位置：PCAP 和导出对话框位于程序目录；设置和世界数据库位于旁边的 `data/`。

S5 性格目前用三场“龙棋士→恶魔狼”验证，不能据此保证所有精灵、所有遭遇或其他赛季。精灵坐标使用最后收到的位置，不模拟游走。

## 运行

使用 Windows、Python 3.10 或以上（含 tkinter）；实时抓包需要另行安装 Npcap。离线回放不需要 Npcap。

```powershell
python -m pip install -r requirements.txt
python roco_nearby_analyzer.py
```

也可以使用 `无黑框启动.vbs`。先开始采集，再登录游戏或等待游戏完成重连。没有收到并验证连接密钥时，工具会等待，不绕过加密。

## 可选本地资源

公开源码不包含个人抓包、存档、游戏地图、角色图标或从第三方工具提取的整套数据表。

没有本地资源时，程序使用雷达；缺少映射的身高/体重百分比显示未知，名称可能显示配置编号或形态编号。声音、原始体型数值、已知战斗性格和 PCAP 解析仍可使用。

如你有可使用的本地资源，可以放入以下位置。这些文件默认被 `.gitignore` 排除：

| 路径 | 用途 |
|---|---|
| `species_names.json` | 配置编号到名称的映射 |
| `data_tables/creature_size_ranges.json` | 形态的身高、体重范围 |
| `data_tables/npc_to_form.json` | 场景配置编号到形态编号 |
| `map_assets/maps.json` 与地图图片 | 地图投影及底图 |
| `assets/roco.png`、`assets/roco.ico` | 窗口和 EXE 的自定义图标 |

资源格式说明见各目录 README。代码许可证不授予第三方素材、游戏角色或外部数据的使用权。

## 构建 EXE

```powershell
python -m pip install -r requirements-build.txt
python build_exe.py
```

生成同目录的 `ROCO附近精灵.exe`。构建只打包代码与存在的资源目录，不包含 `data/` 或抓包；自定义图标存在时自动使用。源码运行时更改即时生效，EXE 需要重新构建。

独立 EXE 无需 Python；实时抓包仍需 Npcap。移动程序时，请连同旁边的 `data/` 一起保留。

## 验证

```powershell
python -m unittest discover -s tests
```

默认检查不依赖本地游戏资源，GitHub Actions 执行同一命令。已有完整资源后，可执行更多资源相关测试：

```powershell
python -m unittest discover -s tests/resource_dependent
```

单独目录 `tests/resource_dependent` 中的原测试需要匹配的数据表与底图。真实个人 PCAP 不随仓库分发。

## 开源与资料来源

程序代码拟按 MIT 许可发布，见 [LICENSE](LICENSE)；可选第三方资源不属于该代码许可。依赖与本地资源说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。项目主页：https://github.com/Vicuna1277/ROCO-Nearby-Pet
