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
        "API keys are stored in the .env file on the device. For security, existing values are never displayed. To change a key, delete it and add a new one. Some plugins may require a restart after changing keys.": "API 密钥保存在设备上的 .env 文件中。出于安全考虑，现有值不会显示。要修改密钥，请先删除再添加新的密钥。某些插件在修改密钥后可能需要重启。"
    };

    const dynamicRules = [
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

    function applyTranslations(language = getLanguage()) {
        if (!document.body) {
            return;
        }

        applying = true;
        document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
        document.documentElement.setAttribute("data-inkypi-language", language);
        ensureLanguageToggle(language);
        walkAndTranslate(document.body, language);
        applying = false;
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
        applyTranslations();

        const observer = new MutationObserver(scheduleApply);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            characterData: true,
            attributes: true,
            attributeFilter: ["title", "placeholder", "aria-label", "alt"]
        });
    });

    window.InkyPiI18n = {
        getLanguage,
        setLanguage,
        applyTranslations,
        translateValue
    };
})();
