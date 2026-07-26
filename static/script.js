// SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0

// ==========================================
// 1. 全局配置与初始化
// ==========================================
// 配置 marked.js 的参数
marked.setOptions({
    breaks: true, // 允许回车换行
    gfm: true     // 开启 GitHub 风格的 Markdown
});

// ECharts 全局变量
// ==========================================
// 1. 全局配置与图表初始化逻辑
// ==========================================
let bloomChart;
let questionHistory = []; 
let scoreHistory = [];    

// 封装一个独立的初始化函数，方便多处调用
function initOrUpdateChart() {
    const chartDom = document.getElementById('bloom-chart');
    if (!chartDom) return;

    // 如果还没初始化过，则进行初始化
    if (!bloomChart) {
        bloomChart = echarts.init(chartDom);
    }

    // AI辅助生成：deepseek-v3-2-251201，2026-04-24 核心逻辑：从隐藏的 JSON 标签中提取历史记录并转换为坐标
    try {
        const jsonText = document.getElementById('student-records-data').textContent;
        const rawData = JSON.parse(jsonText);
        
        // 只有在数组为空时才从 HTML 抓取历史，防止重复叠加
        if (questionHistory.length === 0) {
           const currentTaskTitle = typeof CURRENT_TASK_TITLE !== 'undefined' ? CURRENT_TASK_TITLE : "未知任务";// 注意：如果在js文件中，请确保此变量已定义
            const scoreMapping = { 'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1, 'F': 0 };
            
            rawData.forEach(row => {
                // 数据库记录：[时间, 姓名, 学号, 问题, 回复, 评分, 反馈, 任务名...]
                const taskName = row.length > 7 ? row[7] : '未分类早期任务';
                const currentTaskTitle = typeof CURRENT_TASK_TITLE !== 'undefined' ? CURRENT_TASK_TITLE : "未知任务";
                const scoreStr = (row.length > 5 ? row[5] : "").trim();
                if (scoreStr && scoreMapping[scoreStr] !== undefined) {
                    questionHistory.push('Q' + (questionHistory.length + 1));
                    scoreHistory.push(scoreMapping[scoreStr]);
                }
            });
        }
    } catch (e) {
        console.error("解析历史数据失败:", e);
    }

    const option = {
        tooltip: { trigger: 'axis' },
        grid: { left: '10%', right: '10%', bottom: '15%', top: '15%', containLabel: true },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: questionHistory,
            axisLabel: { fontSize: 10 }
        },
        yAxis: {
            type: 'value',
            min: 0, max: 5, interval: 1,
            axisLabel: {
                fontSize: 10,
                formatter: value => ['F.无效', 'E.记忆', 'D.理解', 'C.应用', 'B.分析', 'A.创造'][value]
            }
        },
        series: [{
            type: 'line',
            smooth: true,
            data: scoreHistory,
            areaStyle: { opacity: 0.1 },
            itemStyle: { color: '#5156be' },
            lineStyle: { width: 3 }
        }]
    };

    bloomChart.setOption(option);
    
    // AI辅助生成：deepseek-v3-2-251201，2026-04-24 解决“不显示”的关键：强制触发一次大小调整
    setTimeout(() => {
        bloomChart.resize();
    }, 100);
}

// AI辅助生成：deepseek-v3-2-251201，2026-04-09 登录后的双重启动保障
document.addEventListener("DOMContentLoaded", initOrUpdateChart);
window.onload = () => {
    if(bloomChart) bloomChart.resize();
};

// 窗口缩放时自动调整
window.onresize = () => {
    if(bloomChart) bloomChart.resize();
};


// ==========================================
// 2. 核心交互逻辑 (聊天与流式渲染)
// ==========================================

// 监听回车键发送
function handleKeyPress(e) {
    if (e.key === 'Enter') { 
        sendMessage(); 
    }
}

// 强制滚动到底部的魔法函数
function scrollToBottom() {
    const chatHistory = document.getElementById('chat-history');
    if(chatHistory) {
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
}

// 统一向屏幕追加消息气泡的辅助函数
function appendMessage(text, sender, elementId = null) {
    const chatHistory = document.getElementById('chat-history');
    const rowDiv = document.createElement('div');
    // 根据是学生还是AI，应用不同的 CSS 类
    rowDiv.className = `message-row ${sender}-row`;
    rowDiv.style.display = 'flex';
    rowDiv.style.marginBottom = sender === 'user' ? '5px' : '25px';
    rowDiv.style.justifyContent = sender === 'user' ? 'flex-end' : 'flex-start';

    const bubbleDiv = document.createElement('div');
    bubbleDiv.className = `bubble ${sender}-bubble`;
    
    if (elementId) {
        bubbleDiv.id = elementId;
        bubbleDiv.innerHTML = '<span style="color:#999; font-style:italic;">导师思考中...</span>';
    } else {
        bubbleDiv.innerText = text;
    }

    rowDiv.appendChild(bubbleDiv);
    chatHistory.appendChild(rowDiv);
    scrollToBottom();
}


// AI辅助生成：deepseek-v3-2-251201，2026-04-25 1. 在文件最顶部（函数外）增加全局锁，防止狂点
// AI辅助生成：deepseek-v3-2-251201，2026-04-25 1. 设置全局锁，防止点击瞬间产生多个请求
// AI辅助生成：deepseek-v3-2-251201，2026-04-25 声明全局调试锁
// AI辅助生成：deepseek-v3-2-251201，2026-04-25 确保有这行全局变量
window.isAiThinking = false;

async function sendMessage() {
    const inputField = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const message = inputField.value.trim();

    if (!message || window.isAiThinking) return;

    window.isAiThinking = true;
    inputField.disabled = true;
    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    }

    appendMessage(message, 'user');
    const aiMessageId = 'ai-msg-' + Date.now();
    appendMessage('', 'ai', aiMessageId);
    inputField.value = '';

    // AI辅助生成：deepseek-v3-2-251201，2026-04-25 核心防暴死：定义网络中断控制器与看门狗
    const abortController = new AbortController();
    let watchdog;

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message }),
            signal: abortController.signal // 绑定中断信号
        });

        if (!response.ok) throw new Error("HTTP 状态异常");

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let aiFullText = '';
        let buffer = '';

        // 🐶 看门狗逻辑：如果 15 秒没有任何数据传来，强行斩断 Fetch 请求！
        const resetWatchdog = () => {
            clearTimeout(watchdog);
            watchdog = setTimeout(() => {
                console.error("[DEBUG] 🐶 触发看门狗：大模型超过15秒装死，强行切断！");
                abortController.abort(); // 这会立刻抛出异常并跳入 catch 块！
            }, 20000); 
        };

        resetWatchdog(); // 启动看门狗

        while (true) {
            const { done, value } = await reader.read();
            resetWatchdog(); // 只要收到活着的数据，就重置看门狗
            
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;

            if (buffer.includes('[DONE_MARKER]') || buffer.includes('[DONE]')) {
                break; 
            }

            let lines = buffer.split('\n');
            buffer = lines.pop(); 

            for (let line of lines) {
                line = line.trim();
                if (line.startsWith('data:')) {
                    let jsonStr = line.substring(5).trim();
                    if (jsonStr === '[DONE]') break; 
                    try {
                        let dataObj = JSON.parse(jsonStr);
                        aiFullText += (dataObj.text || dataObj.content || "");
                    } catch (e) {}
                } else if (line && !line.includes('data:')) {
                    aiFullText += line;
                }
            }

            const aiBubble = document.getElementById(aiMessageId);
            if (aiBubble) {
                let safeText = aiFullText.replace(/\[DONE_MARKER\]/g, '').replace(/\[DONE\]/g, '');
                aiBubble.innerHTML = DOMPurify.sanitize(marked.parse(safeText));
                scrollToBottom();
            }
        }

        clearTimeout(watchdog); // 正常结束，关掉看门狗

        // 终极公式渲染
        const finalBubble = document.getElementById(aiMessageId);
        if (finalBubble) {
            let finalSafeText = aiFullText.replace(/\[DONE_MARKER\]/g, '').replace(/\[DONE\]/g, '');
            finalBubble.innerHTML = DOMPurify.sanitize(marked.parse(finalSafeText));
            if (window.MathJax) {
                MathJax.typesetPromise([finalBubble]).then(() => scrollToBottom());
            }
        }

        if (typeof updateCognitiveChart === 'function') {
            updateCognitiveChart(message);
        }

    } catch (error) {
        console.error("网络中断或看门狗触发:", error);
        const aiBubble = document.getElementById(aiMessageId);
       
    } finally {
        // AI辅助生成：deepseek-v3-2-251201，2026-04-25 终极护盾：无论是正常结束还是被看门狗咬断，按钮百分之一万会解锁！
        clearTimeout(watchdog);
        window.isAiThinking = false;
        inputField.disabled = false;
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '发送';
        }
        inputField.focus();
        scrollToBottom();
    }
}
// ==========================================
// 3. 认知轨迹图表的异步更新逻辑
// ==========================================
async function updateCognitiveChart(studentQuestion) {
    try {
        // 偷偷调用后端的 AI 评估接口 (/api/ai_evaluate)
        const response = await fetch('/api/ai_evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: studentQuestion,
                task_title: document.title // 或者传特定的任务变量
            })
        });
        
        const data = await response.json();
        
        // 将后端的 A-F 评级转化为 1-5 的数值坐标以便图表绘制
        const scoreMapping = { 'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1, 'F': 0 };
        const numericScore = scoreMapping[data.score] || 0;
        
        // 更新数据数组
        questionHistory.push('Q' + (questionHistory.length + 1));
        scoreHistory.push(numericScore);
        
        // 通知 ECharts 刷新图表动画
        if (bloomChart) {
            bloomChart.setOption({
                xAxis: { data: questionHistory },
                series: [{ data: scoreHistory }]
            });
        }
        
    } catch (error) {
        console.error("无法获取认知评分：", error);
    }
}


// 辅助功能：点击提问脚手架，自动填充输入框
function fillInput(templateText) {
    const inputField = document.getElementById('user-input');
    if (!inputField || inputField.disabled) return;
    
    inputField.value = templateText;
    inputField.focus();
    
    // 可选：高亮选中文本框里的 "___" 方便学生直接打字替换
    const underscoreIndex = templateText.indexOf('___');
    if (underscoreIndex !== -1) {
        inputField.setSelectionRange(underscoreIndex, underscoreIndex + 3);
    }
}
