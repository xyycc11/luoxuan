# 本轮角色与怪物图片来源

2026-10-22。按用户要求使用已有原图，不调用图片生成、不重绘、不补全。本文件不将原神官方美术或用户提供图片声明为本项目原创/开源。

## 旅人

- `characters/aether.jpg`：原神3.8特别节目封面中的空Q版，截取左下人物区域，原图1280×720，裁切框(0,395,205,720)。来源：[节目封面](https://img.youtube.com/vi/jlivBvu3Jrc/maxresdefault.jpg)。不是本项目生成的角色，也不是独立透明立绘。
- `characters/lumine.jpg`：原神4.8前瞻宣传图中的荧Q版，原图1920×1080，裁切框(460,635,695,1040)。来源：[宣传图转载](https://img1.gamersky.com/image2024/07/20240703_qy_372_5/gamersky_01origin_01_202473175112B.jpg)，[对应前瞻报道](https://game.3loumao.org/132810812?language=zh-tw)。不是本项目生成的角色，也不是独立透明立绘。

## 双守卫

- `characters/venti.jpg`：用户本次提供的温迪图片，保留人物区域，去掉右下Logo所在的画面区域；只做矩形裁切，无重绘。
- `characters/varka.jpg`：用户本次提供的法尔伽图片，保留中间人物区域，排除左侧姓名/星级及右侧分享/UID区域；只做矩形裁切，无重绘。不是自行绘制的Q版。
- 输入原文件不修改。具体输入、裁切框和SHA-256保存在本地交付清单，不将剪贴板路径写入公开资源。

## 怪物

2026-10-23新增`monsters/shafu.jpg`：用户本轮提供的莎芙头像，原文件直接复制，不生成、不重绘。附件原名`codex-clipboard-4e414f8a-7932-4be5-9b69-8855a6c4030d.jpg`；“莎芙（龙龙最可爱）”及滑板秒杀为项目原创同人设定，不冒充官方剧情。图片权利仍归原作者/权利方，项目代码许可不覆盖它。

八张均为游戏内怪物图标，256×256原图，不使用生成怪物。数据名来自[genshin-db图片索引](https://github.com/theBowja/genshin-db/blob/main/src/data/image/enemies.json)，原图由[Yatta](https://gi.yatta.moe/)托管。

| 文件 | 游戏资源标识 |
| --- | --- |
| monsters/hilichurl.png | UI_MonsterIcon_Hili_None_01 |
| monsters/wooden-mitachurl.png | UI_MonsterIcon_Brute_None_01 |
| monsters/pyro-slime.png | UI_MonsterIcon_Slime_Fire_02 |
| monsters/anemo-samachurl.png | UI_MonsterIcon_Shaman_Wind_01 |
| monsters/ruin-guard.png | UI_MonsterIcon_Defender_Noner_01 |
| monsters/unusual-hilichurl.png | UI_MonsterIcon_Hili_Wei |
| monsters/ruin-grader.png | UI_MonsterIcon_Konungmathr |
| monsters/abyss-lector.png | UI_MonsterIcon_Invoker_Deacon_Electric_01 |

原图地址格式：`https://gi.yatta.moe/assets/UI/monster/<游戏资源标识>.png`。七曜首领是项目原创简化规则，外观借用游戏雷系深渊咏者图标，不代表游戏中存在同名官方首领。

## 使用范围说明

角色与怪物美术的权利归原权利方；公开可见不等于获得通用开源授权。按用户要求用于非官方学习项目展示，并经用户授权随学习仓库上传GitHub；不主张取得商业使用授权。项目自己的代码许可不覆盖这些第三方美术。旧风城背景沿用此前已有插画，本轮没有生成新背景或角色。
