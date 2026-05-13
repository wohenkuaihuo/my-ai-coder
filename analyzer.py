SYSTEM_PROMPT = """
你是一位资深的 Java 首席架构师，擅长分析复杂的企业级业务系统。
我会为你提供一段从 Java 源代码中提取的 JSON 格式元数据。
请你基于这些元数据，完成以下任务：
1. 业务功能推断：用一句话描述这个类的核心业务职责。
2. 逻辑链分析：分析方法之间的潜在调用关系或业务流程。
3. 架构风险评估：根据字段和方法设计，指出可能存在的风险（如高耦合、缺少校验等）。
4. 扩展建议：如果后续要增加“抽检”功能，这个类需要做哪些改动？

请使用专业的 Markdown 格式输出。
"""

import os
import json
from extractor import extract_java_semantics  # 引用之前的提取函数
from config_manager import create_openai_client, get_active_chat_model

def analyze_code_structure(file_path):
    # 1. 获取结构化数据
    print(f"正在解析文件: {file_path} ...")
    metadata = extract_java_semantics(file_path)
    metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False)

    # 2. 构造 Prompt
    user_content = f"这是从 Java 文件中提取的元数据：\n{metadata_json}"

    # 3. 调用 LLM
    print("正在请求 AI 架构师进行深度分析...")
    client = create_openai_client()
    chat_model = get_active_chat_model()
    response = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        temperature=0.3 # 保持输出的确定性和专业性
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    # 测试你的 Service 类
    report = analyze_code_structure("EmployeeService.java")
    
    # 将分析报告存入你的 Obsidian 知识库
    with open("Project_Analysis_Report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("分析完成！报告已生成。")
