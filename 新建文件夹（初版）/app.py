from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
import csv
import os
from datetime import datetime
from openai import OpenAI
import requests
import json

app = Flask(__name__)
# 设置用于加密 session 的密钥，实际上线时换成更复杂的随机字符串
app.secret_key = 'super_secret_key_for_research_project' 

# ================= 核心配置区 =================
# 实验数据保存路径
LOG_FILE = 'experiment_data.csv'
USER_DB = 'users.csv'  # 🌟 新增：用户账户数据库文件
TASK_FILE = 'task_config.json'  # 🌟 新增：用于存储当前学习任务的文件，以供教师发布任务

# AI 大模型 API 配置 (这里以 DeepSeek 或通义千问等兼容 OpenAI 格式的接口为例)
# 在跑通全流程前，可以先留空。如果填了真实的 Key，系统就会真正连网思考

API_KEY = "a488812c-f62c-4644-b427-7cfa14114676" 

#api_key = os.getenv('ARK_API_KEY')

API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions" # 根据申请的模型平台替换
MODEL_NAME = "ep-m-20260322104554-lfntl" # 🌟 填入对应的模型名称，必须和云端平台上看到的一模一样！（这里的模型是 Doubao-Seed-2.0-mini）


'''
豆包的话不允许直接写模型名字！ 它要求你必须在火山引擎的控制台里，为模型创建一个“接入点”，然后拿这个以 ep- 开头的接入点 ID 当作模型名字填进代码里。

修改方法：
登录火山引擎控制台，找到“在线推理”页面。
找到你想要用的模型（比如 Doubao-pro-4k），点击**“创建接入点”**。
创建成功后，你会得到一串类似于 ep-20240321123456-abcde 的字符串。这才是你代码里需要的 MODEL_NAME！
'''

# ================= 数据库初始化与验证 =================

def init_user_db():
    """如果不存在用户库，自动初始化并写入测试账号"""
    if not os.path.isfile(USER_DB):
        with open(USER_DB, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            # 定义表头：账号, 密码, 角色, 真实姓名
            writer.writerow(['username', 'password', 'role', 'real_name'])
            # 写入两个默认测试账号
            writer.writerow(['admin', '123456', 'teacher', '李老师'])
            writer.writerow(['2026001', '123456', 'student', '张三'])

def verify_login(username, password):
    """验证登录信息并返回用户信息字典，失败返回 None"""
    if not os.path.isfile(USER_DB):
        init_user_db()
        
    with open(USER_DB, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['username'] == username and row['password'] == password:
                return row # 找到了匹配的用户，返回包含其所有信息的字典
    return None

# ================= AI 核心处理模块 =================

# ================= 核心配置区 =================

'''
# Ollama 本地默认服务地址
OLLAMA_API_URL = "http://localhost:11434/api/chat"

# ⚠️ 关键：请改成你真正在 Ollama 里下载的模型名字
#  qwen3-vl-lite:latest 这个模型比较小，能跑起来
MODEL_NAME = "qwen3-vl-lite:latest" 
'''

'''
# ================= 1. 云端 API 配置区 =================
# ⚠️ 这里以兼容性最好的通用 OpenAI 格式为例
# 建议去 硅基流动(SiliconFlow) 申请免费 API Key，或者使用阿里云百炼、DeepSeek
API_URL = "https://ark.cn-beijing.volces.com/api/v3/responses" # 以硅基流动为例
API_KEY = "a488812c-f62c-4644-b427-7cfa14114676" # 🌟 填入你申请的真实 API Key
MODEL_NAME = "ep-m-20260322104554-lfntl" # 🌟 填入对应的模型名称
'''

USER_DB = 'users.csv'
LOG_FILE = 'experiment_data.csv'


# ================= AI 核心处理模块 =================

def call_ai_api(user_input, student_name):
    """
    调用本地 Ollama 模型，注入元认知提问策略的 System Prompt
    后期云端也是一样的，只要把请求地址和认证信息改成云端的就行，提示词完全复用不变。
    """
    # 1. 结构化的元认知提示词（提示词工程的落地处）
    system_prompt = f"""
    你是一位经验丰富的苏格拉底式导师。当前正在辅导学生：{student_name}。
    【核心任务】：引导学生针对给定的阅读材料或物理现象，提出更深刻、更有探究价值的问题。
    【核心目标】：绝对不直接告诉学生科学原理或正确答案！而是通过诊断学生当前提问的层次，利用循序渐进的引导和认知冲突，帮助他们修补认知漏洞，提升元认知能力与高阶思维。

    【执行工作流 (Chain-of-Thought)】 
    在回复学生之前，你必须在后台默默完成以下三步思考（切记：这三步是你的内部推理，绝对不允许输出给学生！）：
    > 第一步：隐性诊断。分析学生当前的提问处于什么层次（是只关注了表面现象？是缺乏前提条件？还是已经触及了核心机制？）。
    > 第二步：匹配梯度支架 (Pose-EMD)。根据诊断结果，在心里准备一个难度递进的启发策略：
        - 基础激活：引导关注材料中的具体细节或矛盾点。
        - 深入纠偏：引导学生将现象与具体条件结合，例如“如果改变某一个条件，现象还会发生吗？”
        - 认知冲突：举出一个极端的反例，打破学生的固有认知。
    > 第三步：拓展发散 (Pose-SD)。如果学生已经提出了好问题，思考如何引导他进行反思或提出一个相似/完全不同的新问题。

    【最终输出要求（严格遵守）】
    完成内部思考后，你只能向学生输出一段自然、亲切的对话回复！
    1. 每次回复只抛出 1 到 2 个启发式问题，保持互动的“半自由”状态，像真实的聊天一样。
    2. 最小干预原则：绝对不能直接解释现象背后的科学知识。
    3. 动态调节：如果学生输入的内容太短、说“不知道”，或者提问太浅（如只问“为什么”），请给出明确的调节性提示：“你能试着换个角度，或者加上具体的条件来提问吗？”
    4. 语气亲切、鼓励，富有耐心，避免生硬死板的机器人感。
    """

    # 2. 构造符合 Ollama 接口规范的请求体,这里云端调用也是完全一样的，只要把 MODEL_NAME 替换成云端的模型名字就行，提示词
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "stream": False,  # 设为 False，让模型思考完一次性返回完整结果
        "options": {
            "temperature": 0.7 # 控制创造力，0.7 比较适合有引导性的教育对话
        }
    }

    try:
        # 3. 发送本地请求
        # 本地推理可能需要几秒到十几秒不等，所以 timeout 设置稍微长一点
        '''
        #本地调用
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=60) 
        '''
        
        #远程云端调用
        response = requests.post(API_URL, headers={'Authorization': f'Bearer {API_KEY}'}, json=payload, stream=True, timeout=60)
        
        
        response.raise_for_status()
        
        # 4. 解析 Ollama 返回的结果
        result = response.json()
        return result['message']['content']
        
    except requests.exceptions.ConnectionError:
        return "【系统提示】无法连接到本地大脑，请检查电脑上的 Ollama 软件是否已启动并在后台运行。"
    except Exception as e:
        print(f"本地 AI 调用异常: {e}")
        return "AI 思考时遇到了点小问题，请重新发送试试。"


# ================= 路由控制模块 =================

init_user_db() # 初始化用户数据库


# ================= 登录模块 =================
@app.route('/', methods=['GET', 'POST'])
def login():
    """统一登录网关与角色分发"""
    if request.method == 'POST':
        # 获取前端传来的数据
        username = request.form.get('username')
        password = request.form.get('user_id') # 前端的密码框 name 我们没改，依然是 user_id
        
        if not username or not password:
            return render_template('login.html', error="账号和密码不能为空")

        # 🌟 核心：去 CSV 数据库比对校验
        user_info = verify_login(username, password)
        
        if user_info:
            # 登录成功，将信息存入 session
            session['username'] = user_info['username'] # 登录账号
            session['real_name'] = user_info['real_name'] # 真实姓名
            session['role'] = user_info['role'] # 角色身份
            
            # 🌟 核心：根据数据库里配置的角色进行跳转
            if user_info['role'] == 'teacher':
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_chat'))
        else:
            # 登录失败，返回错误提示
            return render_template('login.html', error="账号或密码错误，请检查后重试！")
            
    # GET 请求直接渲染登录页
    return render_template('login.html')


# ================= 学生交互端 =================
@app.route('/student')
def student_chat():
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))
        
    current_task = get_current_task() # 🌟 获取最新任务

    # ================= 💡 新增：读取当前学生的专属聊天记录与批改反馈 =================
    student_id = session.get('username') # 在 login 路由里，username 存的就是学号
    my_chat_history = []
    csv_file = 'experiment_data.csv'
    
    # 遍历 CSV，把属于这个学生的所有数据全部捞出来
    import os, csv # 防止你最上面没引包，这里再写一次以防万一
    if os.path.exists(csv_file):
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                # 检查是不是这名学生的数据 (row[2] 是学号)
                if len(row) >= 5 and row[2] == student_id:  
                    my_chat_history.append(row)
    # =====================================================================

    return render_template('student.html', 
                           student_name=session['real_name'],
                           current_task=current_task,
                           chat_history=my_chat_history  # 👈 核心：把捞出来的记录传给前端
                           )
'''
def student_chat():
    """学生交互端"""
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))
    
    # 将真实姓名传给前端显示
    return render_template('student.html', 
                           student_name=session['real_name'], 
                           student_id=session['username'])
'''



'''
# ================= 本地大模型调用 =================
@app.route('/chat', methods=['POST'])
def chat():
    """处理前端发来的流式交互请求及数据记录"""
    # 1. 安全校验
    if 'role' not in session or session['role'] != 'student':
        return jsonify({"error": "未授权访问，请先登录"}), 403

    data = request.json
    user_input = data.get('message')
    student_name = session.get('real_name')
    student_account = session.get('username')
    
    if not user_input:
        return jsonify({"error": "提问内容不能为空"}), 400

    # 2. 构造本地大模型的提示词和请求体
    system_prompt = f"""
    你是一位经验丰富的苏格拉底式导师。当前正在辅导学生：{student_name}。
    你的核心任务是：引导学生针对特定的阅读材料提出更深刻的问题;你的核心目标不是直接告诉学生正确答案，而是通过诊断学生的错因，利用循序渐进的提问和认知冲突，引导学生自我发现错误、修补认知漏洞，并提升其元认知能力与高阶思维。请保持语气鼓励、自然，避免生硬死板的机器人感。

    【执行工作流(Chain-of-Thought)】 在回复学生之前，你必须严格按照以下步骤在后台进行推理和生成：
>
    第一步：隐性错因诊断（先诊断，后提问） 请先对学生的回答进行内部错因分析（例如：是混淆了公式、忽略了隐含条件、计算失误，还是逻辑推理断裂？）。确立导致该错误的根本概念盲点.(注：此分析仅作为你后续提问的依据，不要直接全盘输出给学生)。
>
    第二步:生成三级梯度提示（Pose-EMD策略） 基于上述错因，请为学生提供三个难度递进的启发式问题。问题必须遵循认知规律，从低阶思维（记忆/理解）向高阶思维（分析/评价）跃迁：
        提示1（简单难度 / 基础概念激活）：针对错因，提问相关的基础定义或公式。例如：“解决这道题我们需要用到哪个核心定理？它的标准表达式是什么？”
        提示2（中等难度 / 深入应用与纠偏）：引导学生将基础概念代入当前题目，发现矛盾。例如：“结合你刚才说的公式，如果已知条件是X和Y，你目前的计算步骤里是否遗漏了哪一项？”
        提示3（困难难度 / 认知冲突与举一反三）：设置一个触发事件（如反例或极端情况）制造认知冲突。例如：“如果按照你的解法，当变量变为负数时，结果还会成立吗？你能举个反例验证一下吗？”
>
    第三步:元认知与发散思维拓展（Pose-SD策略） 当学生在你的引导下修正错误后，为了进一步巩固知识并提升元认知，请提出以下两类进阶任务：
        反思与相似提问（Pose-Similar）：要求学生评估自己的解题策略，并基于原题的数学结构，自己提出一个情境不同但考查知识点相似的数学问题。
        差异化提问（Pose-Different）：要求学生跳出当前框架，改变原题的核心条件或视角，提出一个具有不同运算逻辑或数学主题的全新问题。
    【输出要求】
        不要直接给出计算结果。
        根据学生当前的反馈状态，灵活且自然地抛出上述阶段的提示，一次互动只聚焦1-2个问题，不要一次性把所有问题砸向学生，保持交互的“半自由”状态.
    
    【严格遵守以下规则】：
    1. 最小干预原则：绝对不能直接回答学生关于知识点的问题。
    2. 过程导向原则：你的回复必须是一个“提示”，引导学生反思自己的认知。
    3. 动态支架：如果学生的问题太短或太浅，请给出调节性提示：“你能试着换个角度，或者加上具体的条件来提问吗？”
    4. 语气要求：亲切、鼓励，像一个有耐心的老师。
    """

    payload = {
        "model": MODEL_NAME, # ⚠️ 务必确保与本地 Ollama 跑的模型名字完全一致！
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "stream": True,  # 🌟 核心开关：要求本地模型开启流式传输
        "options": {
            "temperature": 0.7 
        }
    }

    # 3. 定义流式生成器函数
    def generate_stream():
        full_ai_response = "" # 用于在后台偷偷拼接完整的回复以供存盘
        
        try:
            # 向本地 Ollama 发起请求，注意开启 stream=True
            # ollama_api_url = "http://localhost:11434/api/chat" # 本地调用地址
            
            # 远程云端调用地址
            ollama_api_url = "https://your-ollama-api-endpoint.com/api/chat"
            
            response = requests.post(ollama_api_url, json=payload, stream=True, timeout=60)
            response.raise_for_status()
            
            # 遍历模型吐出的每一行数据碎片
            for line in response.iter_lines():
                if line:
                    chunk_data = json.loads(line)
                    
                    
                    # 旧版本（适用于本地 Ollama 模型）： 
                    chunk_text = chunk_data.get('message', {}).get('content', '')
                    

                    # 标准的云端 API 格式：
                    # 云端通常把文字藏在 choices -> delta -> content 里面
                    chunk_text = chunk_data['choices'][0].get('delta', {}).get('content', '')

                    if chunk_text:
                        full_ai_response += chunk_text  # 拼接完整句子
                        yield chunk_text                # 🌟 实时把碎片吐给前端网页
                        
        except Exception as e:
            error_msg = f"\n[AI 大脑连接异常，请检查 Ollama 是否运行。报错: {str(e)}]"
            full_ai_response += error_msg
            yield error_msg

        # 🌟 流式输出全部结束后，再执行写 CSV 数据落盘的操作！(保障科研数据安全)
        try:
            file_exists = os.path.isfile(LOG_FILE)
            with open(LOG_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(['记录时间', '姓名', '学号', '学生提问', 'AI元认知提示'])
                
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([current_time, student_name, student_account, user_input, full_ai_response])
        except Exception as e:
            print(f"写入 CSV 失败: {e}")

    # 4. 使用 stream_with_context 将生成器转化为响应流发给前端
    return Response(stream_with_context(generate_stream()), mimetype='text/plain')
'''


# ================= 任务存取助手函数 =================
def get_current_task():
    """获取当前教师发布的学习任务，如果没有则返回默认值"""
    if os.path.exists(TASK_FILE):
        with open(TASK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"title": "等待老师发布任务...", "content": "请同学们稍安勿躁。"}

def save_current_task(title, content):
    """保存教师发布的新任务"""
    with open(TASK_FILE, 'w', encoding='utf-8') as f:
        json.dump({"title": title, "content": content}, f, ensure_ascii=False)


# ================= 聊天接口 =================
@app.route('/chat', methods=['POST'])
def chat():
    if 'role' not in session or session['role'] != 'student':
        return jsonify({"error": "未授权访问"}), 403
    
    current_task = get_current_task() # 获取当前教师发布的学习任务
    
    # 🌟 新增：拦截机制。如果任务标题还是默认的“等待老师发布...”，则拒绝服务
    if current_task.get('title') == "等待老师发布任务..." or not current_task.get('title'):
        # 直接返回流式格式的错误提示，或者普通的 JSON 错误（前端 script.js 里写过接收错误的逻辑）
        return jsonify({"error": "老师还未发布探究任务，AI 助手暂时处于休眠状态，请耐心等待。"})


    data = request.json
    user_input = data.get('message')
    student_name = session.get('real_name')
    student_account = session.get('username')
    current_task = get_current_task() # 获取当前教师发布的学习任务


    if not user_input:
        return jsonify({"error": "提问内容不能为空"}), 400
    

    # 只有一个基础的、全局通用的系统提示词
    system_prompt = f"""
    你是一位经验丰富的苏格拉底式导师。当前正在辅导学生：{student_name}，当前任务为：{current_task['title']}。
    【当前学习材料】：标题《{current_task['title']}》 - 内容：{current_task['content']}
    【核心任务】：绝对不能直接回答知识点，必须围绕上述【当前学习材料】引导学生提问。提出更深刻、更有探究价值的问题。
    【核心目标】：绝对不直接告诉学生科学原理或正确答案！而是通过诊断学生当前提问的层次，利用循序渐进的引导和认知冲突，帮助他们修补认知漏洞，提升元认知能力与高阶思维。

    【执行工作流 (Chain-of-Thought)】 
    在回复学生之前，你必须在后台默默完成以下三步思考（切记：这三步是你的内部推理，绝对不允许输出给学生！）：
    > 第一步：隐性诊断。分析学生当前的提问处于什么层次（是只关注了表面现象？是缺乏前提条件？还是已经触及了核心机制？）。
    > 第二步：匹配梯度支架 (Pose-EMD)。根据诊断结果，在心里准备一个难度递进的启发策略：
        - 基础激活：引导关注材料中的具体细节或矛盾点。
        - 深入纠偏：引导学生将现象与具体条件结合，例如“如果改变某一个条件，现象还会发生吗？”
        - 认知冲突：举出一个极端的反例，打破学生的固有认知。
    > 第三步：拓展发散 (Pose-SD)。如果学生已经提出了好问题，思考如何引导他进行反思或提出一个相似/完全不同的新问题。

    【最终输出要求（严格遵守）】
    完成内部思考后，你只能向学生输出一段自然、亲切的对话回复！
    1. 每次回复只抛出 1 到 2 个启发式问题，保持互动的“半自由”状态，像真实的聊天一样。
    2. 最小干预原则：绝对不能直接解释现象背后的科学知识。
    3. 动态调节：如果学生输入的内容太短、说“不知道”，或者提问太浅（如只问“为什么”），请给出明确的调节性提示：“你能试着换个角度，或者加上具体的条件来提问吗？”
    4. 语气亲切、鼓励，富有耐心，避免生硬死板的机器人感。
    """

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "stream": True,  # 开启流式传输
        "temperature": 0.7 
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    def generate_stream():
        full_ai_response = ""
        try:
            # 建立云端连接
            response = requests.post(API_URL, headers=headers, json=payload, stream=True, timeout=15)
            response.raise_for_status()
            
            # 持续接收云端发来的文字碎片
            # 持续接收云端发来的文字碎片
            for line in response.iter_lines():
                if line:
                    # 去除首尾的多余空格和换行符，增强兼容性
                    decoded_line = line.decode('utf-8').strip() 
                    
                    # 匹配 data: 开头（注意：这里去掉了冒号后面的空格限制）
                    if decoded_line.startswith("data:"):
                        # 截取 data: 后面的纯 JSON 字符串并去掉前后空格
                        json_str = decoded_line[5:].strip() 
                        
                        if json_str == "[DONE]": # 传输结束标志
                            break
                        try:
                            chunk_data = json.loads(json_str)
                            # 提取文字 (增加了一些安全默认值，防止报错)
                            choices = chunk_data.get('choices', [])
                            if choices:
                                chunk_text = choices[0].get('delta', {}).get('content', '')
                                if chunk_text:
                                    full_ai_response += chunk_text
                                    yield chunk_text # 推送给前端
                        except Exception:
                            # 如果某一个碎片解析失败，直接跳过，不影响后续文字输出
                            continue
                            
        except Exception as e:
            error_msg = f"\n[API 请求异常，请检查网络或密钥。报错: {str(e)}]"
            full_ai_response += error_msg
            yield error_msg

        # 流式传输彻底结束后，把拼好的完整句子写入 CSV（还原为基础的 5 列格式）
        try:
            # 🌟 终极快照机制：安全获取任务标题与完整的【情境材料】
            task_name = "未分类当前任务"
            task_content_str = "" # 新增变量
            try:
                current_task = get_current_task()
                if current_task:
                    if isinstance(current_task, dict):
                        task_name = current_task.get('title', '未分类当前任务')
                        task_content_str = current_task.get('content', '')
                    else:
                        task_name = getattr(current_task, 'title', '未分类当前任务')
                        task_content_str = getattr(current_task, 'content', '')
            except Exception as e:
                print(f"⚠️ 获取任务详情时出现小问题 (已忽略): {e}")

            file_exists = os.path.isfile(LOG_FILE)
            with open(LOG_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if not file_exists:
                    # 💡 扩充至 9 列：新增了最后的“任务背景材料”
                    writer.writerow(['记录时间', '姓名', '学号', '学生提问', 'AI元认知提示', '教师评分', '教师反馈', '所属任务', '任务背景材料'])
                
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                writer.writerow([
                    current_time,           # 第1列
                    student_name,           # 第2列
                    student_account,        # 第3列
                    user_input,             # 第4列
                    full_ai_response,       # 第5列
                    "",                     # 第6列：预留给教师评分
                    "",                     # 第7列：预留给教师反馈
                    task_name,              # 第8列：所属任务标签
                    task_content_str        # 🌟 第9列：完整的任务情境快照！永久保存！
                ])
        except Exception as e:
            print(f"❌ 写入 CSV 彻底失败: {e}")

    # 将生成器封装为响应流返回
    return Response(stream_with_context(generate_stream()), mimetype='text/plain')


# ================= 教师端后台：数据监控大屏 + 任务发布 =================
@app.route('/teacher', methods=['GET', 'POST'])
def teacher_dashboard():
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))
    
    sync_message = None 

    if request.method == 'POST':
        new_title = request.form.get('task_title')
        new_content = request.form.get('task_content')
        if new_title and new_content:
            try:
                # 尝试保存任务
                save_current_task(new_title, new_content)
                # 🌟 2. 保存成功，修改提示信息
                sync_message = "学习任务已成功同步至所有学生端！" 
            except Exception as e:
                # 如果写入文件时发生系统错误，捕获并提示失败
                sync_message = f"同步失败，系统错误：{str(e)}"

    # 读取当前任务
    current_task = get_current_task()
    
    # 🌟 如果老师点击了“发布任务”，拦截表单并保存
    if request.method == 'POST':
        new_title = request.form.get('task_title')
        new_content = request.form.get('task_content')
        if new_title and new_content:
            save_current_task(new_title, new_content)
            
    # 获取当前任务，展示在后台供老师确认
    current_task = get_current_task()
    
    # 🌟 核心逻辑：读取 CSV 实验数据
    records = []
    if os.path.isfile(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader, None) # 跳过表头
            if header:
                for row in reader:
                    # 确保读取的行数据完整 (对应：时间, 姓名, 学号, 提问, AI回复)
                    if len(row) >= 5: 
                        records.append(row)

    records = []
    if os.path.isfile(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader, None) # 跳过表头
            pass
            if header:
                for row in reader:
                    # 确保读取的行数据完整 (对应：时间, 姓名, 学号, 提问, AI回复)
                    if len(row) >= 5: 
                        records.append(row)
    records.reverse()
    
    # 🌟 3. 把 sync_message 这个“信使”打包发送给 teacher.html
    return render_template('teacher.html', 
                           teacher_name=session['real_name'], 
                           records=records,
                           current_task=current_task,
                           sync_message=sync_message) # 新增这行！
'''
def teacher_dashboard():
    """教师端后台：数据监控大屏"""
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))
    
    # 🌟 核心逻辑：读取 CSV 实验数据
    records = []
    if os.path.isfile(LOG_FILE):
        with open(LOG_FILE, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader, None) # 跳过表头
            if header:
                for row in reader:
                    # 确保读取的行数据完整 (对应：时间, 姓名, 学号, 提问, AI回复)
                    if len(row) >= 5: 
                        records.append(row)
                        
    # 将数据倒序，让最新产生的提问排在最上面
    records.reverse()
    
    # 将真实姓名和数据记录传给前端
    return render_template('teacher.html', 
                           teacher_name=session['real_name'], 
                           records=records)
'''


# ================= 退出登录 =================
@app.route('/logout')
def logout():
    """清除会话，安全退出"""
    session.clear() 
    return redirect(url_for('login'))


# ================= 新增：保存教师批阅记录的接口 =================
@app.route('/save_evaluation', methods=['POST'])
def save_evaluation():
    try:
        data = request.get_json()
        target_time = data.get('time')
        target_id = data.get('id')
        score = data.get('score')
        feedback = data.get('feedback')

        csv_file = 'experiment_data.csv'
        updated_rows = []
        
        # 读取现有的 CSV 文件
        if os.path.exists(csv_file):
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    # 如果找到了匹配的【时间】和【学号】，就把评价追加进去
                    if len(row) >= 3 and row[0] == target_time and row[2] == target_id:
                        # 确保这一行有足够的列来存评分和反馈 (扩充到7列)
                        while len(row) < 7:
                            row.append("")
                        row[5] = score
                        row[6] = feedback
                    updated_rows.append(row)
            
            # 顺便检查表头，如果表头还没这两列，自动加上去
            if updated_rows and len(updated_rows[0]) < 7:
                updated_rows[0].extend(["教师评分", "教师反馈"])
                    
            # 把更新后的数据重新覆盖写入 CSV
            with open(csv_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(updated_rows)
                
            return jsonify({"status": "success", "message": "保存成功"})
        else:
            return jsonify({"status": "error", "message": "找不到数据文件"}), 404
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ================= 真实 AI 智能助教评估接口 (情境增强版) =================
@app.route('/api/ai_evaluate', methods=['POST'])
def ai_evaluate():
    data = request.get_json()
    student_question = data.get('question', '')
    # 💡 接收前端传来的具体情境参数
    task_title = data.get('task_title', '未知探究任务')
    task_content = data.get('task_content', '无详细背景说明')

    # 🌟 终极教研 Prompt：强制 AI 结合材料进行深度评估
    system_prompt = f"""你是一位极其严谨的初中科学资深教研员。你的任务是根据具体的【实验探究背景】，深度评估学生提出的问题，并给出定级与针对性反馈。

【当前探究任务/场景】
实验标题：{task_title}
材料背景：{task_content}

【基于情境的评估标准】
⭐ A级（核心探究）：高度贴合上述材料！能直击该场景背后的科学机制，提出包含变量控制、极端假设或深层因果关系的问题。（例：“如果在上述实验中改变...，结果会...？”）
✅ B级（有效相关）：与上述材料紧密相关，逻辑清晰地指出了材料中的具体疑惑，但偏向于对常规现象的求解或机制的浅层询问。
⚠️ C级（表面事实）：与材料弱相关，或者仅停留在名词解释、常识确认上，缺乏在当前情境下深入探究的价值。
❌ D级（无效提问）：与当前的【{task_title}】场景完全无关、偏题、或是无意义的字符。

请严格按照以下 JSON 格式输出结果，绝不能输出任何其他多余的解释、寒暄或Markdown标记：
{{"score": "A或B或C或D", "feedback": "指出该问题在当前情境下的闪光点或不足，并结合该实验给出一步具体的引导建议，不超过60个字"}}"""

    try:
        # ================= 这里保留你之前的云端 API 请求逻辑 =================
        # ⚠️ 请确保下方保留了你真实的 CLOUD_API_URL, CLOUD_API_KEY 等配置
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}" # 如果是智谱/DeepSeek等
        }
        
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"学生提出的问题是：{student_question}"}
            ],
            "temperature": 0.1  # 极低温度，保证打分标准的一致性和严格性
        }
        
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status() 
        
        ai_reply_text = response.json()['choices'][0]['message']['content']
        cleaned_text = ai_reply_text.strip().replace("```json", "").replace("```", "")
        result = json.loads(cleaned_text)
        
        return jsonify({
            "score": result.get("score", "B"), 
            "feedback": result.get("feedback", "AI 分析完毕，请老师核对。")
        })

    except Exception as e:
        print(f"❌ AI 评估出错: {e}")
        return jsonify({"error": "Evaluation Failed"}), 500



if __name__ == '__main__':
    # debug=True 可以在修改代码后自动重启服务器，开发利器
    app.run(debug=True, port=5000)