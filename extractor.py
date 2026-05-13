import json
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

def extract_java_semantics(file_path):
    # 1. 初始化 Java 语言解析器
    JAVA_LANGUAGE = Language(tsjava.language())
    parser = Parser(JAVA_LANGUAGE)
    
    # 2. 读取 Java 文件内容
    with open(file_path, "rb") as f:
        source_code = f.read()
        
    # 3. 生成抽象语法树 (AST)
    tree = parser.parse(source_code)
    root_node = tree.root_node
    
    # 我们要收集的结构化数据
    entity_info = {
        "file_name": file_path,
        "class_name": None,
        "annotations": [],
        "fields": []
    }
    
    # 4. 遍历 AST 提取信息 (简化版的 DFS 遍历)
    for child in root_node.children:
        # 寻找类声明
        if child.type == 'class_declaration':
            for node in child.children:
                # 提取类名
                if node.type == 'identifier':
                    entity_info["class_name"] = source_code[node.start_byte:node.end_byte].decode('utf8')
                
                # 提取类级别的注解 (如 @Table, @Data)
                elif node.type == 'modifiers':
                    for mod in node.children:
                        if mod.type == 'marker_annotation' or mod.type == 'annotation':
                            entity_info["annotations"].append(source_code[mod.start_byte:mod.end_byte].decode('utf8'))
                            
                # 进入类内部提取字段 (Class Body)
                elif node.type == 'class_body':
                    for body_node in node.children:
                        if body_node.type == 'field_declaration':
                            field_data = {"type": "", "name": ""}
                            for f_node in body_node.children:
                                if f_node.type == 'type_identifier' or f_node.type == 'integral_type':
                                    field_data["type"] = source_code[f_node.start_byte:f_node.end_byte].decode('utf8')
                                elif f_node.type == 'variable_declarator':
                                    field_data["name"] = source_code[f_node.start_byte:f_node.end_byte].decode('utf8')
                            if field_data["name"]:
                                entity_info["fields"].append(field_data)

    return entity_info

if __name__ == "__main__":
    # 执行提取并格式化输出
    result = extract_java_semantics("SpotCheck.java")
    print(json.dumps(result, indent=4, ensure_ascii=False))