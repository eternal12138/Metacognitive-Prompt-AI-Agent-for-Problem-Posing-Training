// ==========================================
// 1. 全局配置与初始化
// ==========================================
// 配置 marked.js 的参数
marked.setOptions({
    breaks: true, // 允许回车换行
    gfm: true     // 开启 GitHub 风格的 Markdown
});

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