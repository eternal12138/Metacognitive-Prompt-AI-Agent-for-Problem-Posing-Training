from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
from flask_sqlalchemy import SQLAlchemy
import os
from datetime import datetime
import requests
import json
import io
import csv
from flask import make_response

app = Flask(__name__)
# 设置用于加密 session 的密钥，实际上线时换成更复杂的随机字符串
app.secret_key = 'super_secret_key_for_research_project' 

# ================= 核心配置区 =================
# 实验数据保存路径
LOG_FILE = 'experiment_data.csv'
USER_DB = 'users.csv'  # 🌟 新增：用户账户数据库文件
TASK_FILE = 'task_config.json'  # 🌟 新增：用于存储当前学习任务的文件，以供教师发布任务



# ================= 1. 数据库核心配置 =================
# ================= 1. 数据库核心配置 (双库分离架构) =================
# 默认数据库：专门存放账号密码等核心用户数据
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///user_center.db'
# 绑定数据库：专门存放业务产生的庞大数据（对话、任务）
app.config['SQLALCHEMY_BINDS'] = {
    'business_db': 'sqlite:///chat_data.db'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)



# AI 大模型 API 配置 (这里以 DeepSeek 或通义千问等兼容 OpenAI 格式的接口为例)
# 在跑通全流程前，可以先留空。如果填了真实的 Key，系统就会真正连网思考
# **************

API_KEY = "a488812c-f62c-4644-b427-7cfa14114676" 

#api_key = os.getenv('ARK_API_KEY')

API_URL = "**************" # 根据申请的模型平台替换
MODEL_NAME = "****************************" # 🌟 填入对应的模型名称，必须和云端平台上看到的一模一样！（这里的模型是 Doubao-Seed-2.0-mini）


'''
豆包的话不允许直接写模型名字！ 它要求你必须在火山引擎的控制台里，为模型创建一个“接入点”，然后拿这个以 ep- 开头的接入点 ID 当作模型名字填进代码里。

修改方法：
登录火山引擎控制台，找到“在线推理”页面。
找到你想要用的模型（比如 Doubao-pro-4k），点击**“创建接入点”**。
创建成功后，你会得到一串类似于 ep-20240321123456-abcde 的字符串。这才是你代码里需要的 MODEL_NAME！
'''

# ================= 2. 定义数据库表模型 (ORM) =================
class User(db.Model):
    """用户表 (没有指定 bind，默认存入 user_center.db)"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    real_name = db.Column(db.String(50), nullable=False)
    # 🌟 新增：账号状态开关（默认 True 为正常，False 为冻结）
    is_active = db.Column(db.Boolean, default=True)
    # 🌟 新增：超管权限开关（True 表示有权管理系统用户，默认为 False）
    can_manage_users = db.Column(db.Boolean, default=False)

class TaskConfig(db.Model):
    """任务配置表 (指定存入业务数据库)"""
    __bind_key__ = 'business_db'  # 🌟 核心魔法：把它分流到 business_db
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="等待老师发布任务...")
    content = db.Column(db.Text, nullable=False, default="请同学们稍安勿躁。")
    scaffold = db.Column(db.Text, default="")
    custom_standard = db.Column(db.Text, default="")

class ChatRecord(db.Model):
    """实验记录表 (指定存入业务数据库)"""
    __bind_key__ = 'business_db'  # 🌟 核心魔法：把它分流到 business_db
    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.String(50), nullable=False)
    student_name = db.Column(db.String(50))
    student_id = db.Column(db.String(50))
    question = db.Column(db.Text)
    ai_response = db.Column(db.Text)
    teacher_score = db.Column(db.String(10), default="")
    teacher_feedback = db.Column(db.Text, default="")
    task_name = db.Column(db.String(200))
    task_content = db.Column(db.Text)


# ================= 3. 数据库初始化与平滑迁移辅助函数 =================
with app.app_context():
    # 🌟 一键创建所有数据库和表 (会自动根据 bind_key 在根目录生成 user_center.db 和 chat_data.db)
    db.create_all()
    
    # 用户数据迁移逻辑（针对 user_center.db）
    if not User.query.first():
        if os.path.exists(USER_DB):
            print("🚀 检测到老版本 users.csv，正在迁移至用户中心(user_center.db)...")
            with open(USER_DB, mode='r', encoding='utf-8-sig') as f:
                import csv
                reader = csv.DictReader(f)
                for row in reader:
                    new_user = User(
                        username=row['username'], password=row['password'],
                        role=row['role'], real_name=row['real_name']
                    )
                    db.session.add(new_user)
            db.session.commit()
            print("✅ 用户数据迁移成功！")
        else:
            # 🌟 赋予 admin 创世神权限 (can_manage_users=True)
            super_admin = User(username='admin', password='123456', role='teacher', real_name='超管', can_manage_users=True)
            test_student = User(username='2026001', password='123456', role='student', real_name='张三')
            db.session.add(super_admin)
            db.session.add(test_student)
            db.session.commit()
            
    # 业务数据初始化逻辑（针对 chat_data.db）
    if not TaskConfig.query.get(1):
        db.session.add(TaskConfig(id=1))
        db.session.commit()

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
API_KEY = "a4**************" # 🌟 填入你申请的真实 API Key
MODEL_NAME = "ev**************" # 🌟 填入对应的模型名称
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


# ================= 4. 工具函数 (数据库化) =================
def verify_login(username, password):
    user = User.query.filter_by(username=username, password=password).first()
    if user:
        if not user.is_active: return "FROZEN"
        return {
            'username': user.username, 'real_name': user.real_name, 
            'role': user.role, 'can_manage_users': user.can_manage_users # 🌟 带上超管权限
        }
    return None

def get_current_task():
    """从 SQLite 获取当前任务"""
    task = TaskConfig.query.get(1)
    return {
        "title": task.title, 
        "content": task.content, 
        "scaffold": task.scaffold, 
        "custom_standard": task.custom_standard
    }

def save_current_task(title, content, scaffold="", custom_standard=""):
    """保存新任务到 SQLite"""
    task = TaskConfig.query.get(1)
    task.title = title
    task.content = content
    task.scaffold = scaffold
    task.custom_standard = custom_standard
    db.session.commit()



# ================= 登录模块 =================
@app.route('/', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('user_id')
        user_info = verify_login(username, password)
        # 去数据库比对校验
        user_info = verify_login(username, password)
        
        if user_info == "FROZEN":
            return render_template('login.html', error="🚨 您的账号已被管理员冻结，请联系导师处理！")
        elif user_info:
            # 正常登录逻辑... (保持原样) user_info:
            session['username'] = user_info['username']
            session['real_name'] = user_info['real_name']
            session['role'] = user_info['role']
            session['can_manage_users'] = user_info['can_manage_users'] # 🌟 写入全局会话
            return redirect(url_for('teacher_dashboard' if user_info['role'] == 'teacher' else 'student_chat'))
        else:
            return render_template('login.html', error="账号或密码错误")

    # 🌟 设备嗅探
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent for keyword in ['iphone', 'android', 'mobile'])
    
    # 移动端返回 login_mobile.html (也可以共用，但为了极致体验建议分离)
    template = 'login_mobile.html' if is_mobile else 'login.html'
    return render_template(template)


# ================= 学生交互端 =================
# ================= 学生交互端 (增加移动端判断) =================
@app.route('/student')
def student_chat():
    if 'role' not in session or session['role'] != 'student':
        return redirect(url_for('login'))
        
    current_task = get_current_task()
    student_id = session.get('username')

    # my_chat_history = []
    
  # 🌟 数据库查询：获取该学生的所有聊天记录
    records = ChatRecord.query.filter_by(student_id=student_id).all()
    # 🌟 无缝伪装：把数据库对象转换成前端熟悉的“9列列表”格式，前端一行代码都不用改！
    my_chat_history = [
        [r.time, r.student_name, r.student_id, r.question, r.ai_response, r.teacher_score, r.teacher_feedback, r.task_name, r.task_content]
        for r in records
    ]
    
    # 🌟 设备嗅探
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent for keyword in ['iphone', 'android', 'mobile'])
    
    template = 'student_mobile.html' if is_mobile else 'student.html'
    return render_template(template, 
                           student_name=session['real_name'],
                           current_task=current_task,
                           chat_history=my_chat_history)
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
    你是一位经验丰富的苏格拉底式导师，同时精通布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估。当前正在辅导学生：{student_name}。
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
    """获取当前教师发布的学习任务，包含 RAG 评价标准"""
    if os.path.exists(TASK_FILE):
        with open(TASK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 💡 兜底默认值加入了 custom_standard
    return {
        "title": "等待老师发布任务...", 
        "content": "请同学们稍安勿躁。", 
        "scaffold": "",
        "custom_standard": ""
    }

def save_current_task(title, content, scaffold="", custom_standard=""):
    """保存教师发布的新任务，支持保存提问支架和 RAG 评价标准"""
    with open(TASK_FILE, 'w', encoding='utf-8') as f:
        # 💡 将获取到的自定义标准一起打包写入 JSON 文件
        json.dump({
            "title": title, 
            "content": content,
            "scaffold": scaffold,
            "custom_standard": custom_standard  # 🌟 存入这个关键的 RAG 知识库
        }, f, ensure_ascii=False)


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
    你是一位经验丰富的苏格拉底式导师，同时精通布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估。当前正在辅导学生：{student_name}，当前任务为：{current_task['title']}。
    【当前学习材料】：标题《{current_task['title']}》 - 内容：{current_task['content']}
    【核心任务】：绝对不能直接回答知识点，必须围绕上述【当前学习材料】引导学生提问。提出更深刻、更有探究价值的问题。
    【核心目标】：绝对不直接告诉学生科学原理或正确答案！而是通过诊断学生当前提问的层次，利用循序渐进的引导和认知冲突，帮助他们修补认知漏洞，提供“支架式（Scaffolding）”的升维改进建议，提升元认知能力与高阶思维。不过，涉及到基础知识的层面（比如学生对问题做出了自己的回答而非提出问题）则要先回答的到底正确与否，做出一个评价，再引导学生进行探究。

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


# 🌟 流式传输彻底结束后，把拼好的完整句子安全地写入 SQLite 数据库
        try:
            task_name = "未分类当前任务"
            task_content_str = "" 
            current_task = get_current_task()
            if current_task:
                task_name = current_task.get('title', '未分类当前任务')
                task_content_str = current_task.get('content', '')

            # 获取当前时间
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 创建数据库记录对象
            new_record = ChatRecord(
                time=current_time,
                student_name=student_name,
                student_id=student_account,
                question=user_input,
                ai_response=full_ai_response,
                task_name=task_name,
                task_content=task_content_str
            )
            # 存入数据库
            db.session.add(new_record)
            db.session.commit() # 事务提交，多线程安全！
            
        except Exception as e:
            print(f"❌ 写入数据库彻底失败: {e}")
            db.session.rollback() # 发生异常时回滚，保护数据库安全

    # 将生成器封装为响应流返回
    return Response(stream_with_context(generate_stream()), mimetype='text/plain')


# ================= 教师端后台：数据监控大屏 + 任务发布 =================
# ================= 教师控制台路由 =================
@app.route('/teacher', methods=['GET', 'POST'])
def teacher_dashboard():
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))
    
    # 🌟 1. 处理前端“无刷新”提交任务的 POST 请求
    if request.method == 'POST':
        new_title = request.form.get('task_title')
        new_content = request.form.get('task_content')
        new_scaffold = request.form.get('task_scaffold', '')
        
        custom_standard_text = ""
        clear_rubric = request.form.get('clear_rubric') 
        
        if clear_rubric == 'yes':
            pass 
        else:
            uploaded_file = request.files.get('rubric_file')
            if uploaded_file and uploaded_file.filename != '':
                try:
                    custom_standard_text = uploaded_file.read().decode('utf-8')
                except Exception as e:
                    return jsonify({"status": "error", "message": "文件读取失败"}), 400
            else:
                old_task = get_current_task()
                custom_standard_text = old_task.get('custom_standard', '')

        if new_title and new_content:
            try:
                save_current_task(new_title, new_content, new_scaffold, custom_standard_text)
                return jsonify({"status": "success", "message": "任务及评价标准同步成功！"})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500
        
        return jsonify({"status": "error", "message": "标题和内容不能为空"}), 400

    # 🌟 2. 获取当前任务与历史数据
    current_task = get_current_task()
    # 🌟 数据库查询：获取全班所有实验记录，并按时间倒序排列 (最新在最上)
    db_records = ChatRecord.query.order_by(ChatRecord.id.desc()).all()
    
    # 🌟 伪装成前端需要的列表
    records = [
        [r.time, r.student_name, r.student_id, r.question, r.ai_response, r.teacher_score, r.teacher_feedback, r.task_name, r.task_content]
        for r in db_records
    ]
    
    # 🌟 3. 核心升级：设备嗅探与路由分离！
    user_agent = request.headers.get('User-Agent', '').lower()
    # 判断是否为手机端 (iPhone, Android)
    is_mobile = any(keyword in user_agent for keyword in ['iphone', 'android', 'mobile'])
    
    if is_mobile:
        # 如果是手机，返回专属的移动端 HTML
        return render_template('teacher_mobile.html', 
                               teacher_name=session['real_name'], 
                               records=records,
                               current_task=current_task)
    else:
        # 如果是电脑或平板，返回大屏版 HTML
        return render_template('teacher_pc.html', 
                           teacher_name=session['real_name'],
                           current_task=current_task,
                           records=records,
                           can_manage_users=session.get('can_manage_users', False)) # 🌟 传给前端
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


# ================= 账号管理模块 API (仅限管理员调用) =================
@app.route('/api/users/list', methods=['GET'])
def get_user_list():
    """获取用户列表"""
    if not session.get('can_manage_users'):
        return jsonify({"error": "严重越权：非管理员禁止访问"}), 403
    users = User.query.all()
    # 🌟 修复核心：必须把 can_manage_users 字段也打包发送给前端，前端才能正确判断！
    return jsonify([{
        "id": u.id, 
        "username": u.username, 
        "password": u.password, 
        "real_name": u.real_name, 
        "role": u.role, 
        "is_active": u.is_active,
        "can_manage_users": u.can_manage_users  # <--- 就是漏了这极其关键的一行
    } for u in users])

@app.route('/api/users/add', methods=['POST'])
def add_user():
    """新增账号"""
    data = request.json
    # 检查账号是否重复
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"status": "error", "message": "该学号/账号已存在！"}), 400
    
    new_user = User(
        username=data['username'], password=data['password'],
        role=data['role'], real_name=data['real_name']
    )
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"status": "success", "message": "账号添加成功"})

@app.route('/api/users/toggle_status', methods=['POST'])
def toggle_user_status():
    """冻结/解冻账号"""
    data = request.json
    user = User.query.get(data['id'])
    if user:
        # 防止老师把自己冻结了
        if user.username == session.get('username'):
            return jsonify({"status": "error", "message": "不能冻结自己！"}), 400
        user.is_active = not user.is_active
        db.session.commit()
        return jsonify({"status": "success", "message": "状态已更新"})
    return jsonify({"status": "error", "message": "用户不存在"}), 404

@app.route('/api/users/delete', methods=['POST'])
def delete_user():
    """删除账号及关联的所有实验记录"""
    if not session.get('can_manage_users'):
        return jsonify({"error": "严重越权：非管理员禁止访问"}), 403

    data = request.json
    user = User.query.get(data['id'])
    
    if user:
        if user.username == session.get('username'):
            return jsonify({"status": "error", "message": "不能删除自己！"}), 400
        
        try:
            # 🌟 核心修复：跨库级联删除！
            # 在删除用户之前，先去 ChatRecord 业务表里，把 student_id 等于该账号的所有聊天和批阅记录统统删掉
            ChatRecord.query.filter_by(student_id=user.username).delete()
            
            # 删除用户账号本身
            db.session.delete(user)
            
            # 一次性提交所有更改（SQLAlchemy 会自动安全地处理这两个数据库的修改）
            db.session.commit()
            return jsonify({"status": "success", "message": "账号及其所有实验记录已彻底清除"})
            
        except Exception as e:
            db.session.rollback() # 如果中途报错，立刻回滚，保护数据库安全
            return jsonify({"status": "error", "message": f"删除失败: {str(e)}"}), 500
            
    return jsonify({"status": "error", "message": "用户不存在"}), 404


# ================= 新增：保存教师批阅记录的接口 =================
@app.route('/save_evaluation', methods=['POST'])
def save_evaluation():
    try:
        data = request.get_json()
        # 🌟 数据库更新操作，极其安全和优雅，绝不发生并发冲突！
        record = ChatRecord.query.filter_by(time=data.get('time'), student_id=data.get('id')).first()
        
        if record:
            record.teacher_score = data.get('score')
            record.teacher_feedback = data.get('feedback')
            db.session.commit() # 提交事务
            return jsonify({"status": "success", "message": "保存成功"})
        else:
            return jsonify({"status": "error", "message": "在数据库中找不到该记录"}), 404
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    

# ================= 教师端：导出实验数据接口 =================
@app.route('/export_data')
def export_data():
    # 1. 权限校验
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))

    try:
        # 2. 从 SQLite 数据库中按时间正序拉取所有记录
        records = ChatRecord.query.order_by(ChatRecord.id.asc()).all()

        # 3. 在内存中创建一个虚拟的文本文件
        si = io.StringIO()
        writer = csv.writer(si)

        # 4. 写入表头
        writer.writerow(['记录时间', '姓名', '学号', '学生提问', 'AI元认知提示', '教师评分', '教师反馈', '所属任务', '任务背景材料'])

        # 5. 遍历数据库对象，写入数据
        for r in records:
            writer.writerow([
                r.time, 
                r.student_name, 
                r.student_id, 
                r.question, 
                r.ai_response, 
                r.teacher_score, 
                r.teacher_feedback, 
                r.task_name, 
                r.task_content
            ])

        # 6. 将内存中的文本转化为带有 BOM 的 UTF-8 字节流 (防止 Excel 中文乱码的核心魔法)
        output = make_response(si.getvalue().encode('utf-8-sig'))
        
        # 7. 设置 HTTP 响应头，告诉浏览器“这是一个需要下载的附件”
        output.headers["Content-Disposition"] = "attachment; filename=AI_Tutor_Experiment_Data.csv"
        output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        
        return output
        
    except Exception as e:
        return f"导出数据失败: {str(e)}", 500


# ================= 真实 AI 智能助教评估接口 (情境增强版) =================
# ================= 真实 AI 智能助教评估接口 =================
@app.route('/api/ai_evaluate', methods=['POST'])
def ai_evaluate():
    data = request.get_json()
    student_question = data.get('question', '')
    task_title = data.get('task_title', '未知探究任务')
    task_content = data.get('task_content', '无详细背景说明')

    # 🌟 1. 读取当前任务数据，获取老师可能上传的自定义评价标准 (RAG)
    current_task = get_current_task()
    custom_standard = current_task.get('custom_standard', '').strip()

    # 🌟 2. 智能 Prompt 分流架构
    if custom_standard:
        # =========================================================
        # 【路线 A】RAG 模式：老师上传了自定义标准，强制覆盖默认逻辑
        # =========================================================
        system_prompt = f"""【角色设定】
你是一位精通启发式教学、布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估的资深学科提问评价导师。你的核心任务是：基于特定情境，对学生提出的问题进行多维度诊断与评级，并在建立安全鼓励的氛围下，提供“支架式（Scaffolding）”的升维改进建议。当前任务已开启【自定义评价标准库】(RAG 模式)。
你必须**完全抛弃**自带的任何默认评价体系（如布鲁姆分类法），严格地按照下方老师提供的【自定义评价标准】对学生提出的问题进行定级和反馈。

【探究情境】
- 标题：{task_title}
- 内容：{task_content}

【教师上传的自定义评价标准 (核心依据)】
{custom_standard}

【评估工作流与输出要求】
1. 仔细阅读并理解上方【自定义评价标准】中的各个等级定义。
2. 对照该标准，对学生的问题进行诊断。指出亮点和局限。
3. 基于该标准，给出一句启发式的改进建议。
4. 无论老师的标准分为几级，请你将其智能映射到 A、B、C、D、E、F 这几个前端可识别的字母分级中（最高级映射为A，最低或无效映射为F）。

【严格的 JSON 输出格式】
绝不能包含任何 Markdown 标记。必须严格输出以下 JSON：
{{
  "score": "A或B或C或D或E或F",
  "analysis": "根据老师上传的标准，解释为什么给这个等级。先肯定闪光点，再指出局限。（100字内）",
  "suggestion": "基于此评级，给学生提供一句具体的改进建议。（100字内）"
}}
"""
    else:
        # =========================================================
        # 【路线 B】默认模式：使用之前打磨好的布鲁姆 5 层级标准
        # =========================================================
        system_prompt = f"""【角色设定】
你是一位精通启发式教学、布鲁姆认知目标分类（Bloom's Taxonomy）与问题复杂度评估的资深学科提问评价导师。你的核心任务是：基于特定情境，对学生提出的问题进行多维度诊断与评级，并在建立安全鼓励的氛围下，提供“支架式（Scaffolding）”的升维改进建议。

【输入信息】
- 探究情境/原题：{task_title} - {task_content}
- 待评估的学生问题：（前端将动态传入）

【评估工作流（内部思考过程）】
请在内心严格按照以下四个模块进行深度审视：

模块一：有效性与可解性筛查（底线防守）
- 情境契合度：判断该问题是否紧扣上述背景材料。
- 可解性判断：基于已知条件，该问题是否可解（Solvable）？如果不可解或偏题，指明缺失了什么条件或逻辑漏洞（若不可解，直接评为 F 级）。

模块二：认知层级评级（布鲁姆1-5级）
- Level 1 记忆：仅要求回忆基本事实或提取表面信息（是什么？对不对？）。
- Level 2 理解：要求用自己的话解释、分类或重述信息（有什么特点？）。
- Level 3 应用：要求将规则或公式应用于当前情境进行求解（为什么？怎么做？）。
- Level 4 分析：要求分解结构，寻找深层因果关系、隐藏条件或模式对比（为什么？联系是什么？）。
- Level 5 评价与创造：包含“如果...那么...”的条件假设，改变了原有情境，或引入新变量进行探究设计。

模块三：质量诊断与肯定（情感与逻辑并重）
- 结构复杂度：分析问题是简单的赋值问题，还是包含条件与关系的多步复杂问题。
- 闪光点肯定：找出学生提问中的亮点（如视角独特、注意到了某个隐藏现象等）给予肯定，以建立安全、鼓励的提问环境。

模块四：升维改进建议（定向支架）
- 绝对不要直接给出更高阶的问题！请根据当前层级，提供1-2个启发式建议，引导学生向更高层级（Level 4/5）修改：
  -> 若为 Level 1/2：引导将封闭式转为开放式（如“能否试着把‘这是不是’改成‘哪些因素会影响’？”）。
  -> 若为 Level 3：引导加入控制变量（如“试着改变情境中的某个条件，重新提问”）。
  -> 若为 Level 4/5：肯定其探究价值，引导思考如何通过实验设计或逻辑推理来验证这个假设。

【严格的 JSON 输出格式】
绝不能包含任何 Markdown 标记。必须严格输出以下 JSON：
评级映射：Level 5->A, Level 4->B, Level 3->C, Level 2->D, Level 1->E, 无效/不可解->F。

{{
  "score": "A或B或C或D或E或F",
  "analysis": "先指出闪光点，再简要说明结构复杂度或局限。（100字内）",
  "suggestion": "给出一句启发式的升维改进建议。（100字内）"
}}
"""

    try:
        # ================= 调用云端大模型 API =================
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}" # 请确保你的 API_KEY 变量名正确
        }
        
        payload = {
            "model": MODEL_NAME, # 请确保你的 MODEL_NAME 变量名正确
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
        
        # 🌟 核心修复区：正确解析 JSON 并拼接反馈
        result = json.loads(cleaned_text)

        ai_score = result.get("score", "C")
        analysis = result.get("analysis", "未提供分析")
        suggestion = result.get("suggestion", "未提供建议")

        # 💡 将分析和建议拼接成一段带有换行和图标的完整反馈，传给前端！
        # 💡 增加一个 \n，形成真实的物理空行
        final_feedback = f"{analysis}\n\n💡 导师建议：{suggestion}"
        
        return jsonify({
            "score": ai_score,
            "feedback": final_feedback  
        })

    except Exception as e:
        print(f"❌ AI 评估出错: {e}")
        return jsonify({"error": "Evaluation Failed"}), 500



if __name__ == '__main__':
    # debug=True 可以在修改代码后自动重启服务器，开发利器
    app.run(debug=True, port=5000)
