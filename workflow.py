from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from llama_index.core import StorageContext, load_index_from_storage
from llama_index.core import Settings
import os
import json
import re # 记得在文件顶部 import
import subprocess

# 【新增】引入我们自己写的 AST 提取器
from extractor import extract_java_semantics

from config_manager import (
    create_embedding_model,
    create_openai_client,
    get_active_chat_model,
    get_project_workspace_dir,
)

from langgraph.checkpoint.memory import MemorySaver

# 【新增】架构师的系统提示词
ARCHITECT_SYSTEM_PROMPT = """你是一位拥有多年经验的资深 Java 首席架构师。
你的任务是基于 Librarian（检索专员）提供的【本地代码与规则上下文】，为用户的新需求制定详细的代码修改与执行计划。

⚠️ 严格要求：
1. 必须完全遵循上下文中提供的数据库表结构、字段名、状态机枚举（如 0-待检, 1-合格等）。
2. 必须复用上下文中已存在的 Java 实体和类名。
3. 如果发现用户的需求与现有防作弊规则（如唯一约束）冲突，必须在计划中提出拦截方案。

请输出一份结构化的 Markdown 实施计划，必须包含：
1. **需求理解**：一句话总结核心目标。
2. **领域模型变更**：是否需要改表？涉及哪些核心类？
3. **详细改动清单 (Implementation Plan)**：详细列出需要新建或修改的文件路径，以及每个文件需要增加的核心逻辑（方法签名与核心伪代码）。
"""

# 【新增】程序员的系统提示词
CODER_SYSTEM_PROMPT = """你是一位执行力极强、编码严谨的资深 Java 开发工程师。
你的任务是严格根据架构师的执行计划，编写最终的 Java 代码。

⚠️ 输出规范：
你必须且只能输出一个合法的 JSON 数组，绝不能包含任何多余的解释。
- "file_path": 必须是符合 Maven 标准的相对路径（例如 "src/main/java/com/plaza/cleaning/service/impl/SpotCheckServiceImpl.java"）。
- "code": 完整的 Java 代码内容。

示例格式：
[
  {
    "file_path": "src/main/java/com/plaza/cleaning/service/impl/SpotCheckServiceImpl.java",
    "code": "package com.plaza.cleaning.service.impl;\n\nimport org.springframework.stereotype.Service;\n\n@Service\npublic class SpotCheckServiceImpl { ... }"
  }
]
"""

# 定义整个工作流中流转的数据结构（也就是 Agent 们的共享黑板）
class AgentState(TypedDict):

    user_request: str        # 用户原始需求 (如："新增抽检功能")
    target_project_path: str  # 【新增】目标项目的物理路径
    context_data: str        # Librarian 收集到的项目上下文 (我们之前脚本输出的 JSON/Markdown)
    execution_plan: str      # Architect 制定的修改计划
    generated_code: Dict[str, str] # Coder 生成的代码 (文件名 -> 代码内容)
    qa_errors: List[str]     # QA 跑测试发现的错误信息
    current_step: str        # 当前走到哪一步了 (用于日志追踪)
    retry_count: int         # 重试次数，防止无限死循环
    # 【新增】人类审核相关的状态
    human_feedback: str       # 人类的修改意见
    approval_status: str      # "approved" (通过) 或 "rejected" (打回)

# 1. 检索专员节点
def librarian_node(state: AgentState):
    print("📚 [Librarian] 正在通过本地向量库进行语义检索...")
    user_request = state.get("user_request")
    project_path = state.get("target_project_path")
    if not project_path:
        return {"context_data": "❌ 未指定目标项目路径。", "current_step": "librarian"}
    
    # 定义知识库的存放位置 (与我们上一节建库的路径保持一致)
    persist_dir = os.path.join(get_project_workspace_dir(project_path), "vector_store")
    legacy_persist_dir = os.path.join(project_path, ".ai_coder_knowledge", "vector_store")
    if not os.path.exists(persist_dir) and os.path.exists(legacy_persist_dir):
        persist_dir = legacy_persist_dir
    
    if not os.path.exists(persist_dir):
        return {"context_data": "❌ 未找到项目的向量索引，请先在界面执行初始化扫描！", "current_step": "librarian"}
        
    try:
        # 1. 配置 Embedding 模型 (必须与建库时一致)
        Settings.embed_model = create_embedding_model()
        Settings.llm = None
        
        # 2. 从本地磁盘加载索引库
        storage_context = StorageContext.from_defaults(persist_dir=persist_dir)
        index = load_index_from_storage(storage_context)
        
        # 3. 核心：执行 Top-K 向量检索 (找出最相关的 5 个代码/文档块)
        retriever = index.as_retriever(similarity_top_k=5)
        nodes = retriever.retrieve(user_request)
        
        # 4. 组装上下文黑板
        context_parts = []
        for i, node in enumerate(nodes):
            file_name = node.metadata.get('file_name', '未知文件')
            context_parts.append(f"--- 匹配片段 {i+1} (来源: {file_name}) ---\n{node.get_content()}")
            
        final_context = "\n\n".join(context_parts)
        print(f"   -> ✅ 成功检索到 {len(nodes)} 个相关代码块，已投喂给架构师。")
        
        return {"context_data": final_context, "current_step": "librarian"}
        
    except Exception as e:
        print(f"   -> ❌ 检索失败: {e}")
        return {"context_data": f"检索出错: {e}", "current_step": "librarian"}

# 2. 架构师节点
def architect_node(state: AgentState):
    print("\n📐 [Architect] 正在阅读 Librarian 提供的上下文，并深思熟虑中...")
    
    user_request = state.get("user_request")
    context_data = state.get("context_data")
    
    # 将需求和本地上下文组装在一起，喂给 LLM
    user_content = f"【用户需求】:\n{user_request}\n\n【本地工作区上下文】:\n{context_data}\n\n请基于上述信息，给出严格的执行计划。"

    # 【新增】如果有被人类打回的反馈意见，追加到 Prompt 中，让大模型纠正
    feedback = state.get("human_feedback")
    if feedback and state.get("approval_status") == "rejected":
        user_content += f"⚠️【重要：人类架构师的驳回意见】:\n你之前的计划被驳回，用户的修改要求是：\n{feedback}\n\n请严格基于上述意见，重新输出调整后的执行计划！\n\n"
    else:
        user_content += "请基于上述信息，给出严格的执行计划。\n\n"
    
    try:
        # 调用大模型 (推荐继续使用 Claude 3.5 Sonnet 或 GPT-4o)
        client = create_openai_client()
        chat_model = get_active_chat_model()
        response = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2 # 保持架构设计的严谨性，不发散
        )
        
        plan = response.choices[0].message.content
        print(f"   -> ✅ 架构方案设计完毕！(共 {len(plan)} 字)")
        
        # 【彩蛋】为了让你直观看到效果，我们把生成的计划保存到本地文件中
        with open("Latest_Execution_Plan.md", "w", encoding="utf-8") as f:
            f.write(plan)
        print("   -> 📄 已将详细计划输出至: Latest_Execution_Plan.md")
        
    except Exception as e:
        plan = f"生成计划失败: {e}"
        print(f"   -> ❌ {plan}")
        
    return {"execution_plan": plan, "current_step": "architect"}

# 人工节点
def human_node(state: AgentState):
    # print("\n==================================================")
    # print("👨‍💻 [Human-in-the-Loop] 架构方案已输出至 Latest_Execution_Plan.md")
    # print("==================================================")
    
    # # 阻塞主线程，等待用户在终端输入
    # user_input = input("👉 请审核计划。输入 'y' 确认通过并开始写代码，或直接输入您的修改意见打回重写：\n> ")
    
    # # 判断用户意图
    # if user_input.strip().lower() in ['y', 'yes', 'ok', '好', '没问题']:
    #     print("   -> ✅ 人类架构师已确认，进入编码阶段...")
    #     return {
    #         "approval_status": "approved", 
    #         "human_feedback": "", 
    #         "current_step": "human"
    #     }
    # else:
    #     print("   -> ⚠️ 接收到修改意见，正在打回给 AI 架构师重新规划...")
    #     return {
    #         "approval_status": "rejected", 
    #         "human_feedback": user_input, 
    #         "current_step": "human"
    #     }
    # 这个节点被唤醒时执行，因为交互已经在前端完成了，所以它什么都不用做，直接放行
    print("   -> 👨‍💻 前端审核完毕，状态机恢复流转...")
    return {"current_step": "human"}

def coder_node(state: AgentState):
    retry_count = state.get('retry_count', 0)
    print(f"\n💻 [Coder] 接收到架构计划，正在键盘上疯狂输出... (第 {retry_count + 1} 次尝试)")
    
    plan = state.get("execution_plan")
    context = state.get("context_data")
    qa_errors = state.get("qa_errors", [])
    
    # 组装 Prompt
    user_content = f"【本地上下文】:\n{context}\n\n【架构师执行计划】:\n{plan}\n\n"
    
    # 如果是 QA 打回的重试，带上错误日志
    if qa_errors:
        user_content += f"⚠️【上一次编译/测试报错】:\n{qa_errors[-1]}\n请修复上述错误并重新生成代码！\n"
    
    try:
        # 调用 LLM 进行编码
        client = create_openai_client()
        chat_model = get_active_chat_model()
        response = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": CODER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1 # 编码任务需要极低的温度以保证语法确定性
        )
        
        raw_output = response.choices[0].message.content
        
        # 核心防御逻辑：清洗 LLM 输出，防止它自作主张加了 json 标签
        cleaned_output = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_output.strip(), flags=re.IGNORECASE|re.MULTILINE)
        
        # 解析 JSON
        files_to_write = json.loads(cleaned_output)
        
        generated_code_dict = {}
        
        # 遍历数组，执行物理文件写入
        for file_item in files_to_write:
            file_path = file_item.get("file_path")
            code_content = file_item.get("code")
            
            if file_path and code_content:
                # 确保目录存在 (防范 LLM 生成带有层级路径如 src/main/java/... 的文件)
                os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else '.', exist_ok=True)
                
                # 写入文件
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(code_content)
                
                generated_code_dict[file_path] = code_content
                print(f"   -> 💾 成功生成并保存文件: {file_path}")
                
        return {
            "generated_code": generated_code_dict, 
            "current_step": "coder",
            "retry_count": retry_count + 1
        }
        
    except json.JSONDecodeError as e:
        print(f"   -> ❌ 解析 LLM 输出的 JSON 失败: {e}\n原始输出:\n{raw_output[:200]}...")
        # 格式错误也算作一种报错，交给后续的环路去处理
        return {"qa_errors": [f"输出格式非严格 JSON: {e}"], "retry_count": retry_count + 1}
    except Exception as e:
        print(f"   -> ❌ 编码过程中发生异常: {e}")
        return {"qa_errors": [str(e)], "retry_count": retry_count + 1}

# 4. 测试专员节点
def qa_node(state: AgentState):
    print("\n🧪 [QA] 正在调用 Maven 进行全局编译测试...")
    
    # 假设你的 workflow.py 是放在广场保洁项目根目录运行的，
    # 如果不是，请在这里指定你真实项目的绝对路径
    project_root_dir = "." 
    
    # 使用 Maven 进行编译 (使用 -T 1C 开启单核并发加速，-q 开启安静模式减少无关日志)
    result = subprocess.run(
        ["mvn", "clean", "compile", "-T", "1C"],
        capture_output=True,
        text=True,
        cwd=project_root_dir
    )
    
    errors = []
    if result.returncode != 0:
        # 提取 Maven 的报错日志 (通常带有 [ERROR] 标签)
        error_lines = [line for line in result.stdout.split('\n') if "[ERROR]" in line]
        error_msg = "\n".join(error_lines[:15]) # 只截取前15行核心报错，防止撑爆上下文
        
        errors.append(f"Maven 编译失败:\n{error_msg}")
        print(f"   -> ❌ 编译失败，已捕获 Maven 错误日志，准备打回给 Coder 重试...")
    else:
        print(f"   -> ✅ Maven 编译完美通过！依赖一切正常。")

    max_retries = 3
    current_retry = state.get('retry_count', 0)
    
    if errors and current_retry >= max_retries:
        print(f"\n   -> 🚨 警告：已达到最大自动修复次数 ({max_retries})，请人类架构师介入检查 pom.xml 或导包问题！")
        return {"qa_errors": [], "current_step": "qa"}

    return {"qa_errors": errors, "current_step": "qa"}

# ================= 路由逻辑 =================
# 【新增】人类审核后的路由分发
def route_after_human(state: AgentState) -> str:
    """决定人类审核后的去向"""
    if state.get("approval_status") == "approved":
        return "Coder"      # 同意则去写代码
    else:
        return "Architect"  # 驳回则重回架构师节点

def should_continue(state: AgentState) -> str:
    """决定下一步去哪里：如果 QA 报错就打回给 Coder，如果通过就结束"""
    if len(state.get("qa_errors", [])) > 0:
        return "retry"
    return "end"

# ================= 组装图 =================
# 1. 初始化图对象
workflow = StateGraph[AgentState, None, AgentState, AgentState](AgentState)

# 2. 添加所有节点
workflow.add_node("Librarian", librarian_node)
workflow.add_node("Architect", architect_node)
workflow.add_node("Human", human_node)  # 【新增】
workflow.add_node("Coder", coder_node)
workflow.add_node("QA", qa_node)

# 3. 定义常规连线 (单向流)
workflow.set_entry_point("Librarian")
workflow.add_edge("Librarian", "Architect")
workflow.add_edge("Architect", "Human") # 【修改】Architect 生成后交给 Human
# workflow.add_edge("Architect", "Coder")
workflow.add_edge("Coder", "QA")

# 3. 添加人类审核的条件路由 (自愈环 1 号)
workflow.add_conditional_edges(
    "Human",
    route_after_human,
    {
        "Coder": "Coder",
        "Architect": "Architect"
    }
)

# 4. 定义条件连线 (核心：自愈循环)
# QA 结束后，根据 should_continue 的返回值决定下一站
workflow.add_conditional_edges(
    "QA",
    should_continue,
    {
        "retry": "Coder",  # 如果返回 retry，回到 Coder
        "end": END         # 如果返回 end，流程结束
    }
)

# 【核心改造点 👇】：增加记忆断点引擎
memory = MemorySaver()

# 编译成可执行程序，并设置在 Human 节点【执行前】强行中断挂起
agent_app = workflow.compile(checkpointer=memory, interrupt_before=["Human"])

# 在 workflow.py 底部增加这个暴露的入口函数
# def run_agentic_workflow(user_request: str, project_path: str):
#     """供外部 GUI 调用的统一入口，使用生成器逐步 yield 状态"""
#     initial_state = {
#         "user_request": user_request,
#         "target_project_path": project_path,
#         "retry_count": 0,
#         "qa_errors": []
#     }
    
#     # 逐个产出图的执行状态
#     for output in app.stream(initial_state):
#         for key, value in output.items():
#             yield value # yield 当前节点产生的 state

# ================= 运行测试 =================
if __name__ == "__main__":
    print("🚀 启动 AI 编程助手流水线...")
    
    # 构造初始请求
    initial_state = {
        "user_request": "在广场保洁系统中新增人员抽检的接口",
        "target_project_path": ".",
        "retry_count": 0,
        "qa_errors": []
    }
    
    # 触发执行流
    for output in app.stream(initial_state):
        # 遍历每一步的结果
        for key, value in output.items():
            pass # 节点内已经打印了日志，这里静默处理
            
    print("\n🎉 任务最终完成！")