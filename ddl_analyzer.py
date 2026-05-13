DBA_SYSTEM_PROMPT = """
你是一位拥有 20 年经验的资深 DBA 与业务架构师。
我会提供一段业务系统（广场保洁管理系统）的 MySQL 建表语句（DDL）。
请你深度剖析这段 DDL，并帮我反向推导出其中的“业务规则”，输出一份结构化的 Markdown 报告。

报告必须包含以下 4 个模块：
1. 实体定义：用一句话总结这张表在现实世界对应什么事物。
2. 核心状态机：提取所有表示“状态”、“类型”的字段，列出枚举值，并推测可能的状态流转图（如 待办 -> 处理中 -> 完成）。
3. 强业务规则（重点）：基于 NOT NULL、UNIQUE 唯一索引、默认值等约束，推断系统在录入数据时必须遵循哪些死规定。
4. 实体关联：推断这张表依赖了哪些上游表（根据 xxx_id 字段）。
"""

import os
import re
from config_manager import create_openai_client, get_active_chat_model

def split_ddl_file(file_path):
    """
    将完整的 SQL 文件拆分成单个的 CREATE TABLE 语句块
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 使用正则匹配每一个 CREATE TABLE 语句，直到遇到分号 ;
    # 这里处理得比较简单，适用于标准的导出 DDL
    table_blocks = re.findall(r"(CREATE TABLE.*?;)", content, re.IGNORECASE | re.DOTALL)
    
    tables = {}
    for block in table_blocks:
        # 提取表名
        match = re.search(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[`'\"]?(\w+)[`'\"]?", block, re.IGNORECASE)
        if match:
            table_name = match.group(1)
            tables[table_name] = block.strip()
            
    return tables

def analyze_table_rules(table_name, ddl_content):
    """
    调用 LLM 提取表级别的业务规则
    """
    print(f"🕵️ 正在让 DBA Agent 分析表: {table_name} ...")
    
    # 这里引入你在第二步定义的 Prompt
    DBA_SYSTEM_PROMPT = """你是一位拥有 20 年经验的资深 DBA 与业务架构师。
我会提供一段业务系统（广场保洁管理系统）的 MySQL 建表语句（DDL）。
请你深度剖析这段 DDL，并帮我反向推导出其中的“业务规则”，输出一份结构化的 Markdown 报告。
报告必须包含：1. 实体定义 2. 核心状态机 3. 强业务规则 4. 实体关联。"""

    user_content = f"请分析以下建表语句：\n```sql\n{ddl_content}\n"

    client = create_openai_client()
    chat_model = get_active_chat_model()
    response = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": DBA_SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        temperature=0.1 # 降低发散，保持严谨
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    # 假设你把完整的 SQL 保存为了 plaza_system.sql
    sql_file = "init.sql" 
    
    # 如果文件不存在，可以先创建一个假的用于测试
    if not os.path.exists(sql_file):
        with open(sql_file, "w", encoding="utf-8") as f:
            f.write("""
CREATE TABLE `cln_spot_check` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键',
  `zone_id` varchar(50) NOT NULL COMMENT '区域网格编号',
  `cleaner_id` bigint(20) NOT NULL COMMENT '负责的保洁员ID',
  `inspector_id` bigint(20) NOT NULL COMMENT '质检员ID',
  `status` tinyint(4) NOT NULL DEFAULT '0' COMMENT '状态：0-待检, 1-合格, 2-不合格待整改, 3-复检通过',
  `deduct_points` int(11) DEFAULT '0' COMMENT '扣除绩效分',
  `check_time` datetime DEFAULT NULL COMMENT '抽检时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_zone_time` (`zone_id`,`check_time`) COMMENT '同一区域同一时间只能有一条抽检'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='保洁抽检单表';
            """)
            
    # 1. 拆分表
    tables = split_ddl_file(sql_file)
    print(f"✅ 成功从 SQL 文件中提取出 {len(tables)} 张表。")

    # 2. 逐表分析并保存到 Markdown
    os.makedirs("DDL_Rules", exist_ok=True)
    
    for table_name, ddl in tables.items():
        report = analyze_table_rules(table_name, ddl)
        
        # 将分析结果保存
        with open(f"DDL_Rules/{table_name}_rules.md", "w", encoding="utf-8") as f:
            f.write(f"# 表名：{table_name}\n\n")
            f.write(f"## 原始 DDL\n```sql\n{ddl}\n```\n\n")
            f.write(f"## 业务规则提炼\n{report}")
            
    print("🎉 所有建表语句分析完毕，规则已存入 DDL_Rules 目录！")