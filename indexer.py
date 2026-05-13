import os
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings, StorageContext

from config_manager import create_embedding_model

def build_local_codebase_index(project_path, output_dir):
    """
    扫描本地项目，提取核心代码与 SQL，构建 LlamaIndex 向量索引。
    """
    print("🧠 正在启动向量化引擎，准备解析领域模型与 API 拓扑...")
    
    # 1. 配置 Embedding 模型，统一从本地模型配置读取。
    Settings.embed_model = create_embedding_model()
    
    # 我们不需要在这里配置 LLM（大语言模型），因为建立索引只依赖 Embedding 模型把文本变数字。
    Settings.llm = None 

    # 2. 配置文件阅读器 (忽略无关文件，只抓取核心业务资产)
    # 对于保洁管理系统，Java 代码、Mapper XML 和 SQL DDL 是业务逻辑的绝对核心
    required_exts = [".java", ".xml", ".sql", ".md"]
    exclude_dirs = ["target", ".git", ".idea", "venv", ".ai_coder_knowledge", "node_modules"]
    
    reader = SimpleDirectoryReader(
        input_dir=project_path,
        required_exts=required_exts,
        recursive=True,
        exclude=exclude_dirs
    )
    
    try:
        print("   -> 📂 正在读取本地文件...")
        docs = reader.load_data()
        if not docs:
            return "⚠️ 未在目录中找到可供索引的 Java/SQL 文件。"
            
        print(f"   -> 🔪 成功读取 {len(docs)} 个代码/文档分块，正在进行语义向量化压缩 (这可能需要十几秒)...")
        
        # 3. 构建核心向量索引
        index = VectorStoreIndex.from_documents(docs)
        
        # 4. 持久化存储到工具工作区，避免侵入目标项目目录。
        persist_dir = os.path.join(output_dir, "vector_store")
        index.storage_context.persist(persist_dir=persist_dir)
        
        # 5. 生成一个简要的索引报告
        report_path = os.path.join(output_dir, "03_Index_Report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# 项目语义索引报告...\n")
            
        return f"✅ 业务全貌解析完毕！已将 {len(docs)} 个知识块持久化至本地向量库。"
        
    except Exception as e:
        return f"❌ 构建代码向量索引失败: {str(e)}"

if __name__ == "__main__":
    # 单机测试运行
    result = build_local_codebase_index(".", ".ai_coder_workspace")
    print(result)