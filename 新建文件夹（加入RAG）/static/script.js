// 发送消息的核心函数 (支持流式输出打字机效果)
async function sendMessage() {
    const inputField = document.getElementById('user-input');
    const chatBox = document.getElementById('chat-box');
    const message = inputField.value.trim();
    
    if (!message) return;

    // 1. 在界面上显示学生发送的消息
    chatBox.innerHTML += `<div class="message msg-user">${message}</div>`;
    inputField.value = ''; // 清空输入框
    chatBox.scrollTop = chatBox.scrollHeight;

    // 2. 预先在界面上创建一个空白的 AI 回复框，并给它一个独一无二的 ID
    const msgId = 'ai-msg-' + Date.now();
    chatBox.innerHTML += `<div class="message msg-ai" id="${msgId}">AI 思考中<span style="animation: blink 1s infinite;">...</span></div>`;
    chatBox.scrollTop = chatBox.scrollHeight;
    
    const aiMessageDiv = document.getElementById(msgId); // 获取刚才创建的空白框

    // 3. 发送请求给后端的 Flask 接口
    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });
        
        if (!response.ok) {
            if (response.status === 403) window.location.href = '/'; 
            aiMessageDiv.innerHTML = "请求失败，请检查网络或重新登录。";
            return;
        }

        // 🌟 核心逻辑：开启流式读取
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8"); // 用于将字节解码为文字
        let isFirstChunk = true;

        // 不断循环，直到数据全部读完
        while (true) {
            const { done, value } = await reader.read();
            
            // 如果后端吐完了数据，结束循环
            if (done) {
                // 更新元认知状态指示器
                document.getElementById('meta-status-display').innerHTML = "当前阶段：✅ 调节与深入 (记录成功)";
                break;
            }
            
            // 解码当前的文字碎片
            const chunkText = decoder.decode(value, { stream: true });
            
            // 如果是收到第一句话，先清空“AI 思考中...”的占位符
            if (isFirstChunk) {
                aiMessageDiv.innerHTML = ''; 
                isFirstChunk = false;
            }
            
            // 将文字追加到对话框中 (将换行符 \n 替换为网页换行 <br>)
            aiMessageDiv.innerHTML += chunkText.replace(/\n/g, '<br>');
            
            // 让滚动条时刻跟随最新打出来的字
            chatBox.scrollTop = chatBox.scrollHeight; 
        }

    } catch (error) {
        console.error('Error:', error);
        aiMessageDiv.innerHTML = `<span style="color:red;">网络连接失败，请检查服务器端是否报错。</span>`;
        chatBox.scrollTop = chatBox.scrollHeight;
    }
}


// 配置 marked.js 的参数
marked.setOptions({
    breaks: true, // 允许回车换行
    gfm: true     // 开启 GitHub 风格的 Markdown
});

// 监听回车键发送
function handleKeyPress(event) {
    if (event.key === 'Enter') {
        sendMessage();
    }
}

// 强制滚动到底部的魔法函数
function scrollToBottom() {
    const chatHistory = document.getElementById('chat-history');
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// 发送消息与流式接收核心函数
async function sendMessage() {
    const inputField = document.getElementById('user-input');
    const message = inputField.value.trim();
    const sendBtn = document.getElementById('send-btn');
    
    if (!message) return;

    // 1. 把学生的提问渲染到屏幕上
    appendMessage(message, 'student');
    
    // 清空并临时禁用输入框
    inputField.value = '';
    inputField.disabled = true;
    if(sendBtn) sendBtn.disabled = true;

    // 2. 预先创建一个 AI 的气泡，准备接收流式数据
    const aiMessageId = 'ai-msg-' + Date.now();
    appendMessage('', 'ai', aiMessageId);
    const aiBubbleContent = document.getElementById(aiMessageId);

    try {
        // 3. 发送请求给后端 Flask
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });

        // 处理非正常返回（例如被后端的未发布任务逻辑拦截）
        if (!response.ok) {
            let errorMsg = "请求失败";
            try {
                const errorData = await response.json();
                errorMsg = errorData.error;
            } catch(e) {}
            aiBubbleContent.innerHTML = `<span style="color:#d73a49; font-weight:bold;">❌ ${errorMsg}</span>`;
            scrollToBottom();
            return;
        }

        // 4. 开始接收并解析流式数据
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let aiFullText = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            // 将接收到的二进制碎片解码为文字
            const chunk = decoder.decode(value, { stream: true });
            aiFullText += chunk;
            
            // 实时用 Markdown 渲染积累的文字，并滚动到底部
            aiBubbleContent.innerHTML = marked.parse(aiFullText);
            scrollToBottom();
        }

    } catch (error) {
        console.error("API Error:", error);
        aiBubbleContent.innerHTML = `<span style="color:#d73a49; font-weight:bold;">❌ 网络连接异常，请重试。</span>`;
    } finally {
        // 5. 对话结束后，解除锁定并让光标重新回到输入框
        inputField.disabled = false;
        if(sendBtn) sendBtn.disabled = false;
        inputField.focus();
        scrollToBottom();
    }
}

// 统一向屏幕追加消息气泡的辅助函数
function appendMessage(text, sender, elementId = null) {
    const chatHistory = document.getElementById('chat-history');
    
    const rowDiv = document.createElement('div');
    rowDiv.className = `message-row ${sender}-row`;

    const bubbleDiv = document.createElement('div');
    bubbleDiv.className = `bubble ${sender}-bubble`;
    
    // 给 AI 的气泡打上 ID，方便后续流式更新
    if (elementId) {
        bubbleDiv.id = elementId;
        bubbleDiv.innerHTML = '<span style="color:#999; font-style:italic;">正在思考中...</span>';
    } else {
        bubbleDiv.innerText = text;
    }

    rowDiv.appendChild(bubbleDiv);
    chatHistory.appendChild(rowDiv);
    scrollToBottom();
}


// 假设 aiMessageDiv 是大模型回复的那个聊天气泡框
// 以前是：aiMessageDiv.innerText = fullText;
// 现在改成：
aiMessageDiv.innerHTML = marked.parse(fullText);

// 获取整个聊天记录的容器框
const chatHistory = document.getElementById('chat-history-container'); // 替换成你实际的 ID

// 每次追加文字后，让它的滚动条强制贴到底部
chatHistory.scrollTop = chatHistory.scrollHeight;

// 监听回车键发送
function handleKeyPress(e) {
    if (e.key === 'Enter') { 
        sendMessage(); 
    }
}