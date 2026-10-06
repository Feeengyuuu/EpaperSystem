/* Read detached diagnostic snapshots only; polling never requests content work. */
(() => {
    'use strict';
    const node = id => document.getElementById(id);
    const labels = {
        ready: '运行正常', degraded: '运行中，有降级项', not_ready: '暂未就绪', starting: '启动中',
        committed: '写屏已提交', display_unknown: '写屏状态未知',
        source_stale: '来源缓存已过期', deadline: '任务超时', resource_pressure: '资源不足，等待重试',
        worker_cleanup: '子任务回收异常', provider_failure: '最近取数未成功', retry_wait: '已延期，等待重试',
        isolated_worker_cleanup_failed: '子任务回收异常触发恢复', memory_pressure: '持续内存压力触发恢复',
        refresh_worker_overrun: '刷新任务严重超时触发恢复',
        data_progress_stalled: '后台数据更新进度落后', presentation_progress_stalled: '显示内容更新进度落后',
        display_progress_stalled: '轮播写屏进度落后', stale_cache: '过期缓存', fresh_cache: '有效缓存',
        live: '实时来源', cached: '已缓存', offline: '离线', asleep: '休眠', online: '在线', unknown: '未知',
        scheduler_stalled: '调度心跳超时', scheduler_starting: '调度器正在启动', scheduler_not_started: '调度器尚未启动',
        lifecycle_not_running: '运行周期尚未就绪', config_invalid: '配置不可用', config_degraded: '配置处于降级状态',
        queue_not_accepting: '队列暂不接收任务', queue_full: '任务队列已满', queue_full_stalled: '队列持续阻塞',
        startup_degraded: '启动检查有降级项', disk_low: '存储空间偏低', disk_hard_limit: '存储空间不足',
        cache_lifecycle_disk_hard: '缓存空间达到硬限制', cache_lifecycle_disk_soft: '缓存空间达到软限制',
        disk_status_unavailable: '无法读取存储状态', development_display_unavailable: '开发环境无显示器',
    };
    const label = value => labels[value] || value || '未知';
    const time = value => {
        const date = new Date(value);
        return value && Number.isFinite(date.getTime()) ? date.toLocaleString() : '暂无记录';
    };
    const duration = seconds => {
        if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '未知';
        if (seconds < 60) return `${Math.round(seconds)} 秒`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
        return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟`;
    };
    function text(parent, tag, value) {
        const element = document.createElement(tag);
        element.textContent = value;
        parent.appendChild(element);
        return element;
    }
    function render(body) {
        const runtime = body.runtime || {};
        const parts = body.components || {};
        const display = parts.display || {};
        const scheduler = parts.scheduler || {};
        const queue = parts.queue || {};
        const parallel = parts.parallel_runtime || {};
        const sample = parallel.resource_sample || {};
        node('service-state').textContent = label(body.status);
        node('release').textContent = body.release_id || '版本未知';
        node('uptime').textContent = `本进程已运行 ${duration(body.uptime_seconds)}`;
        node('display-state').textContent = label(display.state);
        node('commit-id').textContent = `提交：${display.commit_id || '暂无记录'}`;
        node('resource-state').textContent = `资源档位：${scheduler.resource_tier || '未知'}`;
        node('queue-state').textContent = `队列 ${queue.depth ?? '?'} / ${queue.capacity ?? '?'} · 执行中：${scheduler.active_intent || '无'}`;
        node('resource-detail').textContent = `可用内存 ${sample.available_mb == null ? '未知' : Math.round(sample.available_mb) + ' MiB'} · swap ${sample.swap_percent == null ? '未知' : sample.swap_percent + '%'}`;
        node('reasons').replaceChildren();
        for (const reason of body.error_codes || []) text(node('reasons'), 'li', `${label(reason)} (${reason})`);
        if (!(body.error_codes || []).length) text(node('reasons'), 'li', '当前没有服务级降级项。来源健康情况请看下表。');
        node('instances').replaceChildren();
        for (const item of runtime.instances || []) {
            const row = text(node('instances'), 'tr', '');
            const name = text(row, 'td', '');
            text(name, 'strong', item.name || item.plugin_id);
            text(name, 'p', item.policy === 'before_display' ? '显示前更新 · 无独立后台周期' :
                item.interval_seconds ? `后台周期 ${duration(item.interval_seconds)}` : '按设定时刻更新');
            const execution = text(row, 'td', '');
            text(execution, 'strong', item.issue ? label(item.issue) : item.last_success_at ? '最近成功后无新失败' : '尚无成功记录');
            text(execution, 'p', `数据成功：${time(item.last_success_at)}`);
            text(execution, 'p', `缓存生成：${time(item.cache_committed_at)}`);
            if (item.last_attempt_at) text(execution, 'p', `最近尝试：${time(item.last_attempt_at)}`);
            if (item.last_failure_at) text(execution, 'p', `最近失败：${time(item.last_failure_at)}`);
            if (item.issue && item.next_retry_at) text(execution, 'p', `计划重试：${time(item.next_retry_at)}`);
            const sourceCell = text(row, 'td', '');
            const source = item.source;
            if (source) {
                text(sourceCell, 'strong', [label(source.state), source.connectivity && label(source.connectivity)].filter(Boolean).join(' · '));
                text(sourceCell, 'p', source.observed_at ? `来源采样：${time(source.observed_at)}（${duration(source.age_seconds)}前）` : '来源采样时间：未提供');
                if (source.fetched_at) text(sourceCell, 'p', `获取 / 汇总：${time(source.fetched_at)}`);
                const media = source.media || {};
                if (media.covers_requested != null) text(sourceCell, 'p', `封面 ${media.covers_available ?? '?'} / ${media.covers_requested}`);
                if (media.metadata_checked != null) text(sourceCell, 'p', `商店元数据缺失 ${media.metadata_missing ?? '?'} / ${media.metadata_checked} 次语言查询`);
            } else text(sourceCell, 'p', '暂无可归属的来源时间；不能用生成时间替代。');
        }
        node('recoveries').replaceChildren();
        for (const event of [...(runtime.recoveries || [])].reverse()) text(node('recoveries'), 'li', `${time(event.at)} · ${label(event.reason)} · ${event.release_id}`);
        if (!(runtime.recoveries || []).length) text(node('recoveries'), 'li', '本功能启用后尚无自动恢复记录。');
        node('components').textContent = JSON.stringify(parts, null, 2);
        node('status-message').textContent = `状态快照：${time(runtime.observed_at)}。时间按当前浏览器时区显示。`;
        node('status-content').hidden = false;
    }
    let timer;
    let busy = false;
    async function refresh() {
        clearTimeout(timer);
        if (busy || document.hidden) return;
        busy = true;
        const controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch('/api/runtime-status', {cache: 'no-store', signal: controller.signal});
            if (response.status === 401 || response.status === 403 || response.redirected) {
                node('status-content').hidden = true;
                node('status-login').hidden = false;
                node('status-message').textContent = '运行详情仅对管理员开放。';
                return;
            }
            if (!response.ok) throw new Error('status_unavailable');
            node('status-login').hidden = true;
            render(await response.json());
        } catch (_) {
            node('status-message').textContent = '暂时无法读取最新状态。下方若有数据，为上一次成功快照。';
        } finally {
            clearTimeout(deadline);
            busy = false;
            timer = setTimeout(refresh, 30000);
        }
    }
    document.addEventListener('visibilitychange', refresh);
    refresh();
})();
