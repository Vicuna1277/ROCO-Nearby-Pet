# ROCO附近精灵

本项目由GPT完成。

洛克王国查看附近精灵，跑图小软件。

支持本地 Npcap 采集和 PCAP 离线回放，提供竖版悬浮雷达、筛选、战斗信息和按世界存档。

## 功能

- 附近精灵：名称、声音、身高、体重、距离与八方向；支持声音和极端身高筛选。
- 战斗信息：独立显示当前敌方，支持已验证的转换消息；性格标注“只适用于S5赛季”。
- 悬浮雷达：候选、收藏、战斗列表，支持缩放与地图模式。
- 世界存档：按世界保存稀有记录和探索路线，支持文件导入导出与清理。
- 默认输出位置：PCAP 和导出对话框位于程序目录；设置和世界数据库位于旁边的 `data/`。


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


## 开源与资料来源

程序代码拟按 MIT 许可发布，见 [LICENSE](LICENSE)；可选第三方资源不属于该代码许可。依赖与本地资源说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。项目主页：https://github.com/Vicuna1277/ROCO-Nearby-Pet
