"""示例插件：演示 kaomoji-assistant 插件 API 的最小完整用法。

能力演示（均可在插件页开关）：
1. 数据源：追加一个「示例」分类，出现在库页分类下拉 / 搜索 / 候选条里；
2. 输出转换：可选地给每次上屏的文本尾部加一个 ☆
   （默认关闭；在插件页启用本插件后，修改本文件旁的 settings.json 把
   "star": true 写进去再「重载」即可体验，或直接调用 api.settings 修改）；
3. 事件：订阅 after_insert，把每次插入写进应用日志。
"""


def setup(api):
    # ---- 1. 数据源 ----
    api.add_source("示例", [
        "(￣▽￣)-c[].",
        "ヽ(°〇°)ﾉ",
        "☆*:.｡.o(≧▽≦)o.｡.:*☆",
        "(๑•̀ㅂ•́)و✧",
        "∑(ﾟДﾟ)",
        "(  ´•̥̥̥ω•̥̥̥`  )",
        "  ( ᐛ )σ",
        "(⁎˃ᆺ˂)",
    ])

    # ---- 2. 输出转换（默认关闭，见 settings.json 的 "star"） ----
    def add_star(text, ctx):
        if api.settings.get("star"):
            return text + " ☆"
        return text

    api.register_transform(add_star, name="尾部加星", priority=200)

    # ---- 3. 事件订阅 ----
    def on_insert(text, ctx):
        api.log("示例插件观察到一次插入：%s" % text)

    api.on("after_insert", on_insert)
