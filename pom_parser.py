import xml.etree.ElementTree as ET
import os
import re
import json

from config_manager import create_openai_client, get_active_chat_model

TECH_EXPERT_PROMPT = """你是一位作风极其严谨的 Java 首席架构师。
我会提供一段从 Maven 多模块项目中【经过递归向上合并后】的完整依赖与属性 JSON 数据。
请你基于这些确凿的数据，生成 Markdown 格式的《项目技术栈评估报告》。

报告必须包含以下结构：
1. **核心框架基线**：
   - 请直接读取 JSON 中的 `resolved_java_version` 字段作为 JDK 版本。如果值为"未找到明确声明"，请注明“基于当前解析链未发现明确 JDK 版本声明”。
   - 推断 Spring Boot 版本（通常在 properties 或父依赖中）。
   - 列出读取到的 POM 解析链 (parsed_files_chain)，证明结论来源。
2. **持久层技术**：基于合并后的 dependencies 提取（如 MyBatis-Plus、JPA 等）。
3. **中间件与扩展**：列出缓存、消息队列等核心依赖。
"""

def generate_tech_stack_report(pom_info, output_dir):
    print("🧠 正在请架构师分析项目的底层技术栈...")
    import json
    user_content = json.dumps(pom_info, ensure_ascii=False)
    client = create_openai_client()
    chat_model = get_active_chat_model()
    
    response = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": TECH_EXPERT_PROMPT},
            {"role": "user", "content": user_content}
        ],
        temperature=0.1
    )
    
    report = response.choices[0].message.content
    
    # 确保存储目录存在
    os.makedirs(".ai_coder_knowledge", exist_ok=True)
   # 强制写入指定的工作区目录
    report_path = os.path.join(output_dir, "01_Tech_Stack.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
        
    print(f"✅ 技术栈分析完毕，已生成规范文档: {report_path}")
    return report

def _strip_ns(xml_string):
    """简单移除 XML 命名空间，避免 ElementTree 解析时需要带长长的前缀"""
    return re.sub(r'\sxmlns="[^"]+"', '', xml_string, count=1)

def extract_pom_info(pom_path="pom.xml", depth=0, max_depth=3):
    """
    递归解析 pom.xml，自动向上合并父级依赖与属性。
    depth 控制递归深度，防止死循环。
    """
    if not os.path.exists(pom_path) or depth > max_depth:
        return None

    try:
        with open(pom_path, 'r', encoding='utf-8') as f:
            xml_data = _strip_ns(f.read())
        root = ET.fromstring(xml_data)
    except Exception as e:
        return {"error": f"解析 {pom_path} 失败: {str(e)}"}

    current_info = {
        "properties": {},
        "dependencies": [],
        "parent_coords": None
    }

    # 1. 抓取当前层级的 Properties
    properties_node = root.find('properties')
    if properties_node is not None:
        for prop in properties_node:
            current_info["properties"][prop.tag] = prop.text

    # 2. 抓取当前层级的 Dependencies
    deps_node = root.find('dependencies')
    if deps_node is not None:
        for dep in deps_node.findall('dependency'):
            group_id = dep.findtext('groupId', '')
            artifact_id = dep.findtext('artifactId', '')
            if "test" not in artifact_id and "lombok" not in artifact_id:
                current_info["dependencies"].append(f"{group_id}:{artifact_id}")

    # 3. 探查 Parent 并触发递归回溯
    parent_node = root.find('parent')
    parent_info = None

    if parent_node is not None:
        # 获取 relativePath，Maven 未标明时默认就是 ../pom.xml
        relative_path = parent_node.findtext('relativePath', '../pom.xml')
        
        # 计算父 POM 的物理绝对路径
        current_dir = os.path.dirname(os.path.abspath(pom_path))
        parent_pom_path = os.path.normpath(os.path.join(current_dir, relative_path))
        
        print(f"   -> 🔄 [解析器] 发现父级节点，正在向上回溯: {os.path.basename(os.path.dirname(parent_pom_path))}/pom.xml")
        
        # 递归调用，获取父级的完整属性
        parent_info = extract_pom_info(parent_pom_path, depth + 1, max_depth)

    # ==========================================
    # 【新增】探查 Modules 并触发向下递归聚合
    # ==========================================
    modules_node = root.find('modules')
    child_modules_info = []
    
    if modules_node is not None:
        for module in modules_node:
            if module.tag == 'module' and module.text:
                # 推算子模块的 pom.xml 绝对路径
                child_pom_path = os.path.normpath(os.path.join(os.path.dirname(pom_path), module.text, 'pom.xml'))
                print(f"   -> 📂 [解析器] 发现子模块，正在向下钻取: {module.text}")
                
                # 递归调用，提取子模块依赖
                child_info = extract_pom_info(child_pom_path, depth + 1, max_depth)
                if child_info and "error" not in child_info:
                    child_modules_info.append(child_info)



    # ==========================================
    # 核心合并规则 (严格按照 Maven 继承与聚合拓扑)
    # ==========================================
    merged_info = {
        "parsed_files_chain": [],
        "properties": {},
        "dependencies": [],
        "resolved_java_version": "未找到明确声明"
    }

    # 1. 【父级打底】如果有父类信息，先用父类信息铺垫
    if parent_info and "error" not in parent_info:
        merged_info["parsed_files_chain"].extend(parent_info.get("parsed_files_chain", []))
        merged_info["properties"].update(parent_info.get("properties", {}))
        merged_info["dependencies"].extend(parent_info.get("dependencies", []))
    
    # 2. 【当前级处理】记录当前路径，并覆盖/追加当前 POM 的配置
    merged_info["parsed_files_chain"].append(pom_path)
    merged_info["properties"].update(current_info["properties"]) # 子模块属性覆盖父模块
    merged_info["dependencies"].extend(current_info["dependencies"])

    # 3. 【子级聚合】如果当前 POM 聚合了子模块，把子模块的依赖和路径也收拢上来
    for child in child_modules_info:
        merged_info["parsed_files_chain"].extend(child.get("parsed_files_chain", []))
        merged_info["dependencies"].extend(child.get("dependencies", []))
        # 注意：子模块的 properties 一般不反向覆盖父模块，所以这里不 update properties
    
    # 4. 【全局清理】对所有汇总上来的 dependencies 进行去重 (保持原有顺序)
    merged_info["dependencies"] = list(dict.fromkeys(merged_info["dependencies"]))
    # 对解析链也去重（防止循环引用或重复记录）
    merged_info["parsed_files_chain"] = list(dict.fromkeys(merged_info["parsed_files_chain"]))

    # 5. 【最终验证】嗅探合并后是否拿到了真实的 JDK 版本
    for key, val in merged_info["properties"].items():
        if val and ('java.version' in key or 'compiler.source' in key or 'jdk.version' in key):
            merged_info["resolved_java_version"] = val

    return merged_info

if __name__ == "__main__":
    # 你可以直接在子模块目录下运行这个脚本测试
    info = extract_pom_info()
    print(json.dumps(info, indent=2, ensure_ascii=False))