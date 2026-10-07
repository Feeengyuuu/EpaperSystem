(function () {
    const STORAGE_KEY = "inkypi-language";
    const DEFAULT_LANGUAGE = "en";
    const SUPPORTED_LANGUAGES = ["en", "zh"];

    const zh = {
        "Back": "返回",
        "Settings": "设置",
        "Download Logs": "下载日志",
        "Reboot": "重启",
        "Shutdown": "关机",
        "Device Name:": "设备名称：",
        "Type something...": "请输入...",
        "Type to search...": "输入以搜索...",
        "Orientation:": "方向：",
        "Horizontal": "横向",
        "Vertical": "纵向",
        "Invert Image": "反转图像",
        "Time Zone:": "时区：",
        "Time Format:": "时间格式：",
        "12 Hour (AM/PM)": "12 小时制（AM/PM）",
        "24 Hour": "24 小时制",
        "Plugin Cycle Interval:": "插件轮换间隔：",
        "Determines how often the display switches to a new plugin.": "决定屏幕多久切换到下一个插件。",
        "Every": "每",
        "Minute": "分钟",
        "Hour": "小时",
        "Day": "天",
        "Log System Stats": "记录系统状态",
        "Image Settings": "图像设置",
        "Saturation:": "饱和度：",
        "Contrast:": "对比度：",
        "Sharpness:": "锐度：",
        "Brightness:": "亮度：",
        "Inky Driver Saturation:": "Inky 驱动饱和度：",
        "Save": "保存",
        "Success!": "成功！",
        "Error!": "错误！",
        "An error occurred while processing your request.": "处理请求时发生错误。",
        "The system is rebooting. The UI will be unavailable until the reboot is complete.": "系统正在重启。重启完成前界面不可用。",
        "The system is shutting down. The UI will remain unavailable until it is manually restarted.": "系统正在关机。手动重新启动前界面不可用。",

        "Plugins": "插件",
        "Sort": "排序",
        "Reorder plugins": "调整插件顺序",
        "Switch view": "切换视图",
        "List": "列表",
        "Grid": "网格",
        "Drag plugins to reorder. Click \"Save\" when done.": "拖拽插件调整顺序。完成后点击“保存”。",
        "Current Image": "当前图像",
        "Toggle Dark Mode": "切换深色模式",
        "API Keys": "API 密钥",
        "Playlists": "播放列表",

        "Requires API Key": "需要 API 密钥",
        "Refresh Settings": "刷新设置",
        "Update Now": "立即更新",
        "Add to Playlist": "添加到播放列表",
        "Save As...": "另存为...",
        "Style": "样式",
        "Frame:": "边框：",
        "Margins:": "边距：",
        "Top": "上",
        "Bottom": "下",
        "Left": "左",
        "Right": "右",
        "Background:": "背景：",
        "Color": "颜色",
        "Image": "图片",
        "Upload Image": "上传图片",
        "Text Color:": "文字颜色：",
        "Playlist:": "播放列表：",
        "Instance Name:": "实例名称：",
        "Failed to update refresh settings": "刷新设置更新失败",

        "Location:": "位置：",
        "Location: ": "位置：",
        "Latitude": "纬度",
        "Longitude": "经度",
        "Select Location": "选择位置",
        "Weather Provider:": "天气数据源：",
        "Units:": "单位：",
        "Imperial (°F)": "英制（°F）",
        "Metric (°C)": "公制（°C）",
        "Standard (K)": "标准（K）",
        "Title:": "标题：",
        "Location": "位置",
        "Custom": "自定义",
        "Display:": "显示：",
        "Display: ": "显示：",
        "Refresh Time": "刷新时间",
        "Metrics": "指标",
        "Weather Graph": "天气图表",
        "Rain Amount": "降雨量",
        "Moon Phase": "月相",
        "Graph icons every": "图标间隔",
        "hours": "小时",
        "Forecast": "预报",
        "days": "天",
        "Use Location Time Zone": "使用位置时区",
        "Use Local Time Zone": "使用本地时区",

        "Refresh": "刷新",
        "Determines how often the data and image should be refreshed.": "决定数据和图像多久刷新一次。",
        "Enter a number": "输入数字",
        "Daily at": "每天在",

        "New Playlist": "新建播放列表",
        "Update Playlist": "更新播放列表",
        "Playlist Name:": "播放列表名称：",
        "Display from": "显示时间从",
        "Display from ": "显示时间从",
        "Delete": "删除",
        "Displayed Now": "正在显示",
        "Edit Refresh Settings": "编辑刷新设置",
        "Display Now": "立即显示",
        "Delete Plugin Instance": "删除插件实例",
        "Click to view full size": "点击查看完整尺寸",
        "Plugin Instance Preview": "插件实例预览",
        "Plugin Icon": "插件图标",
        "Preview": "预览",

        "No API keys configured yet.": "尚未配置 API 密钥。",
        "Add keys below to enable plugin features.": "在下方添加密钥以启用插件功能。",
        "Add API Key": "添加 API 密钥",
        "KEY_NAME": "KEY_NAME",
        "(unchanged)": "（未更改）",
        "Enter value": "输入值",
        "Please enter a value for new API keys": "请为新的 API 密钥输入值",
        "Failed to save API keys": "API 密钥保存失败",
        "Now Playing": "正在播放",
        "Keys": "密钥",
        "Device": "设备",
        "Main": "主导航",
        "Admin": "管理员",
        "Sign in": "登录",
        "Sign out": "退出登录",
        "Sign out?": "要退出登录吗？",
        "You can still view pages, but changes need a sign-in.": "退出后仍可浏览页面，但修改需要重新登录。",
        "Signed in as administrator": "已以管理员身份登录",
        "Toggle theme": "切换主题",
        "Switch to dark mode": "切换到深色模式",
        "Switch to light mode": "切换到浅色模式",
        "Language": "语言",
        "On the display": "屏幕上正在显示",
        "Nothing on the display yet": "屏幕上还没有内容",
        "Refresh now": "立即刷新",
        "Full size": "查看大图",
        "View full size": "查看大图",
        "The e-paper panel takes about 30 seconds to redraw.": "墨水屏刷新一次大约需要 30 秒。",
        "Playlist settings": "播放列表设置",
        "New playlist": "新建播放列表",
        "Add plugin": "添加插件",
        "Active now": "正在生效",
        "Outside its time window": "不在生效时段",
        "No plugins in this playlist yet": "这个播放列表还没有内容",
        "Open a plugin, set it up, then choose Add to Playlist.": "打开一个插件，设置好后选择“添加到播放列表”。",
        "Browse plugins": "浏览插件",
        "No playlists yet": "还没有播放列表",
        "Create a playlist to choose what the display rotates through.": "新建播放列表，决定屏幕轮播哪些内容。",
        "No image yet": "还没有画面",
        "On display": "正在显示",
        "More actions": "更多操作",
        "Updates before each display": "每次显示前更新",
        "Manual refresh": "手动刷新",
        "Every minute": "每分钟",
        "Every hour": "每小时",
        "Every day": "每天",
        "Healthy": "正常",
        "Waiting to retry": "等待重试",
        "Failed": "失败",
        "Failed · showing cache": "失败 · 显示缓存",
        "Edit settings": "编辑设置",
        "Refresh data now": "立即刷新数据",
        "Refresh schedule": "刷新频率",
        "Delete from playlist": "从播放列表删除",
        "Close": "关闭",
        "Name": "名称",
        "Show between": "显示时段",
        "Start time": "开始时间",
        "End time": "结束时间",
        "The playlist rotates only inside this daily window.": "播放列表只在每天的这个时段内轮播。",
        "Cancel": "取消",
        "Confirm": "确认",
        "Delete this item?": "删除这一项？",
        "Delete this playlist?": "删除这个播放列表？",
        "Delete playlist": "删除播放列表",
        "Sending to the display…": "正在发送到屏幕…",
        "Now on the display.": "已显示到屏幕。",
        "Refreshing data…": "正在刷新数据…",
        "Data refreshed.": "数据已刷新。",
        "Deleted.": "已删除。",
        "Refresh schedule saved.": "刷新频率已保存。",
        "Enter a playlist name.": "请输入播放列表名称。",
        "Sign in to make changes.": "修改前请先登录。",
        "Your session changed. Reload the page and try again.": "会话已变化，请刷新页面后重试。",
        "Could not reach the device.": "无法连接到设备。",
        "Something went wrong.": "出了点问题。",
        "The device could not finish this request.": "设备没能完成这个请求。",
        "The current item is not in a playlist, so it cannot be refreshed here.": "当前画面不属于任何播放列表，无法在这里刷新。",
        "Refresh settings are not available.": "刷新设置暂不可用。",
        "Plugin order saved.": "插件顺序已保存。",
        "Pick a plugin to set it up and add it to a playlist.": "选择一个插件进行设置，并添加到播放列表。",
        "Search plugins": "搜索插件",
        "Reorder": "排序",
        "Done": "完成",
        "Drag plugins to reorder. Choose Done to save.": "拖动插件调整顺序，完成后点“完成”保存。",
        "No plugins match": "没有匹配的插件",
        "Try a different name.": "换个名称试试。",
        "Editing": "正在编辑",
        "API key set": "已设置密钥",
        "API key not set": "未设置密钥",
        "View playlist": "查看播放列表",
        "Display update queued": "已加入显示队列",
        "Scheduled refresh configured.": "已添加到播放列表。",
        "Saved.": "已保存。",
        "Display palette": "显示配色",
        "Auto day/night": "自动日夜切换",
        "Deep night": "深夜",
        "Keys are stored on the device. Saved values are never shown again.": "密钥保存在设备上，保存后不会再显示。",
        "Add variable": "添加变量",
        "Set": "已设置",
        "Not set": "未设置",
        "Used by": "用于",
        "Get key": "获取密钥",
        "Replace": "更换",
        "Set key": "设置密钥",
        "Replace key": "更换密钥",
        "Used by your playlists": "播放列表用到的服务",
        "Plugins in your playlists can use these services. Some work without a key, depending on their settings.": "播放列表里的插件会用到这些服务。部分插件按设置不同，也可以不用密钥。",
        "Configured": "已配置",
        "No keys configured yet": "还没有配置密钥",
        "Set a key below to turn on the plugins that need it.": "在下方设置密钥，启用需要它的插件。",
        "More services": "更多服务",
        "Search services": "搜索服务",
        "No services match": "没有匹配的服务",
        "Try another name.": "换个名称试试。",
        "Other variables": "其他变量",
        "Variables in the env file that no known service uses.": "env 文件中不属于已知服务的变量。",
        "Empty": "空值",
        "No other variables": "没有其他变量",
        "Use Add variable for a key that is not listed above.": "上面没有列出的密钥，可以用“添加变量”添加。",
        "Some plugins pick up a changed key only after the next refresh or a restart.": "部分插件要到下次刷新或重启后才会使用新密钥。",
        "Variable name": "变量名",
        "Value": "值",
        "The value is saved to the device and is not shown again.": "值会保存到设备上，之后不再显示。",
        "Show value while typing": "输入时显示内容",
        "Use letters, digits and underscores, starting with a letter.": "只能用字母、数字和下划线，并以字母开头。",
        "Enter a value.": "请输入值。",
        "Key saved.": "密钥已保存。",
        "Key deleted.": "密钥已删除。",
        "Delete this key?": "删除这个密钥？",
        "just now": "刚刚",
        "in a moment": "马上",
        "API keys are stored in the .env file on the device. For security, existing values are never displayed. To change a key, delete it and add a new one. Some plugins may require a restart after changing keys.": "API 密钥保存在设备上的 .env 文件中。出于安全考虑，现有值不会显示。要修改密钥，请先删除再添加新的密钥。某些插件在修改密钥后可能需要重启。"
    };

    const dynamicRules = [
        {
            match: /^(\d+) min ago$/u,
            zh: (match) => `${match[1]} 分钟前`
        },
        {
            match: /^(\d+) h ago$/u,
            zh: (match) => `${match[1]} 小时前`
        },
        {
            match: /^(\d+) d ago$/u,
            zh: (match) => `${match[1]} 天前`
        },
        {
            match: /^in (\d+) min$/u,
            zh: (match) => `${match[1]} 分钟后`
        },
        {
            match: /^in (\d+) h$/u,
            zh: (match) => `${match[1]} 小时后`
        },
        {
            match: /^Updated (.+)$/u,
            zh: (match) => `更新于 ${translateValue(match[1], "zh")}`
        },
        {
            match: /^Every (\d+) (minute|hour|day)s$/u,
            zh: (match) => `每 ${match[1]} ${{ minute: "分钟", hour: "小时", day: "天" }[match[2]]}`
        },
        {
            match: /^Updated plugin instance (.+)\.$/u,
            zh: (match) => `已更新实例 ${match[1]}。`
        },
        {
            match: /^(.+) will be removed from the device\. Plugins that use it stop working until a new key is set\.$/u,
            zh: (match) => `${match[1]} 将从设备上删除。用到它的插件在设置新密钥前将无法工作。`
        },
        {
            match: /^Daily at (.+)$/u,
            zh: (match) => `每天 ${match[1]}`
        },
        {
            match: /^“(.+)” will be removed from (.+)\. Its settings are deleted too\.$/u,
            zh: (match) => `“${match[1]}”将从 ${match[2]} 中移除，它的设置也会一并删除。`
        },
        {
            match: /^“(.+)” and every item in it will be deleted\.$/u,
            zh: (match) => `“${match[1]}”及其中的所有内容都会被删除。`
        },
        {
            match: /^Success!\s*(.*)$/u,
            zh: (match) => `成功！${match[1] || ""}`
        },
        {
            match: /^Error!\s*(.*)$/u,
            zh: (match) => `错误！${match[1] || ""}`
        },
        {
            match: /^Refreshed (.+)$/u,
            zh: (match) => `已刷新 ${match[1]}`
        },
        {
            match: /^Plugin: (.+) \| Instance: (.+)$/u,
            zh: (match) => `插件：${match[1]} | 实例：${match[2]}`
        }
    ];

    const textNodeOriginals = new WeakMap();
    const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "CODE"]);

    function getLanguage() {
        const stored = localStorage.getItem(STORAGE_KEY);
        return SUPPORTED_LANGUAGES.includes(stored) ? stored : DEFAULT_LANGUAGE;
    }

    function setLanguage(language) {
        localStorage.setItem(STORAGE_KEY, language);
        applyTranslations(language);
    }

    function normalize(text) {
        return text.replace(/\s+/g, " ").trim();
    }

    function splitWhitespace(text) {
        return {
            leading: text.match(/^\s*/u)[0],
            trailing: text.match(/\s*$/u)[0]
        };
    }

    function translateValue(original, language) {
        if (language !== "zh") {
            return original;
        }

        const normalized = normalize(original);
        if (!normalized) {
            return original;
        }

        if (zh[normalized]) {
            const { leading, trailing } = splitWhitespace(original);
            return `${leading}${zh[normalized]}${trailing}`;
        }

        for (const rule of dynamicRules) {
            const match = normalized.match(rule.match);
            if (match) {
                const { leading, trailing } = splitWhitespace(original);
                return `${leading}${rule.zh(match)}${trailing}`;
            }
        }

        return original;
    }

    function shouldSkipTextNode(node) {
        const parent = node.parentElement;
        if (!parent) {
            return true;
        }

        if (SKIP_TAGS.has(parent.tagName)) {
            return true;
        }

        if (parent.closest("[data-i18n-skip]")) {
            return true;
        }

        return false;
    }

    function translateTextNode(node, language) {
        if (shouldSkipTextNode(node)) {
            return;
        }

        if (!textNodeOriginals.has(node)) {
            textNodeOriginals.set(node, node.nodeValue);
        }

        const original = textNodeOriginals.get(node);
        const translated = translateValue(original, language);
        if (node.nodeValue !== translated) {
            node.nodeValue = translated;
        }
    }

    function translateAttributes(element, language) {
        if (element.closest?.("[data-i18n-skip]")) {
            return;
        }

        const attrs = ["title", "placeholder", "aria-label", "alt"];
        attrs.forEach((attr) => {
            if (!element.hasAttribute(attr)) {
                return;
            }

            const originalAttr = `data-i18n-original-${attr}`;
            if (!element.hasAttribute(originalAttr)) {
                element.setAttribute(originalAttr, element.getAttribute(attr));
            }

            const original = element.getAttribute(originalAttr);
            const translated = translateValue(original, language);
            if (element.getAttribute(attr) !== translated) {
                element.setAttribute(attr, translated);
            }
        });
    }

    function walkAndTranslate(root, language) {
        const textWalker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node = textWalker.nextNode();
        while (node) {
            translateTextNode(node, language);
            node = textWalker.nextNode();
        }

        if (root.nodeType === Node.ELEMENT_NODE) {
            translateAttributes(root, language);
        }

        root.querySelectorAll?.("*").forEach((element) => translateAttributes(element, language));
    }

    function ensureLanguageToggle(language) {
        let button = document.getElementById("languageToggle");
        if (!button) {
            button = document.createElement("button");
            button.type = "button";
            button.id = "languageToggle";
            button.className = "language-toggle";
            button.setAttribute("data-i18n-skip", "true");
            button.setAttribute("aria-label", "Language");
            button.addEventListener("click", () => {
                setLanguage(getLanguage() === "zh" ? "en" : "zh");
            });

            const target =
                document.querySelector(".header") ||
                document.querySelector(".app-header .header-content") ||
                document.querySelector(".frame") ||
                document.body;

            target.appendChild(button);
        }

        button.textContent = language === "zh" ? "EN" : "中文";
        button.title = language === "zh" ? "Switch to English" : "切换到中文";
    }

    let applying = false;
    let pendingApply = null;
    let translationObserver = null;
    const translationObserverOptions = {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["title", "placeholder", "aria-label", "alt"]
    };

    function observeTranslationMutations() {
        if (translationObserver && document.body) {
            translationObserver.observe(document.body, translationObserverOptions);
        }
    }

    function applyTranslations(language = getLanguage()) {
        if (!document.body) {
            return;
        }

        translationObserver?.disconnect();
        applying = true;
        try {
            document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
            document.documentElement.setAttribute("data-inkypi-language", language);
            ensureLanguageToggle(language);
            walkAndTranslate(document.body, language);
        } finally {
            applying = false;
            observeTranslationMutations();
        }
    }

    function refreshOriginalsFromMutations(mutations) {
        mutations.forEach((mutation) => {
            if (mutation.type === "characterData") {
                textNodeOriginals.set(mutation.target, mutation.target.nodeValue);
                return;
            }

            if (mutation.type === "attributes" && mutation.target instanceof Element) {
                const attr = mutation.attributeName;
                if (!attr) {
                    return;
                }

                const originalAttr = `data-i18n-original-${attr}`;
                mutation.target.setAttribute(originalAttr, mutation.target.getAttribute(attr) || "");
            }
        });
    }

    function scheduleApply(mutations) {
        if (applying) {
            return;
        }

        if (mutations) {
            refreshOriginalsFromMutations(mutations);
        }

        window.clearTimeout(pendingApply);
        pendingApply = window.setTimeout(() => applyTranslations(), 50);
    }

    document.addEventListener("DOMContentLoaded", () => {
        translationObserver = new MutationObserver(scheduleApply);
        applyTranslations();
    });

    window.InkyPiI18n = {
        getLanguage,
        setLanguage,
        applyTranslations,
        translateValue
    };
})();
