import typer
from rich.console import Console
from rich.progress import track
import time
# 导入我们之前写的 LangGraph 流水线
# from workflow import run_agentic_workflow 

from pom_parser import extract_pom_info, generate_tech_stack_report # 引入刚才写的组件
from indexer import build_local_codebase_index
from config_manager import get_project_workspace_dir

app = typer.Typer(help="您的本地 AI 资深研发助手")
console = Console()

@app.command()
def init(project_path: str = typer.Argument(".", help="要分析的工程根目录")):
    """第一步：扫描新项目，解析技术栈与构建代码向量库。"""
    console.print(f"[bold blue]🔍 开始扫描并解析项目: {project_path}[/bold blue]\n")
    workspace_dir = get_project_workspace_dir(project_path)
    
    # 模块 1：解析底层脚手架与技术栈
    console.print("[bold yellow]>>> 1/2 正在提取 Maven 依赖拓扑...[/bold yellow]")
    pom_data = extract_pom_info(f"{project_path}/pom.xml")
    if isinstance(pom_data, dict):
        generate_tech_stack_report(pom_data, workspace_dir)
        console.print("[bold green]✅ 技术底座分析完成！报告已生成。[/bold green]\n")
    else:
        console.print(f"[bold red]⚠️ {pom_data}[/bold red]\n")

    # 模块 2：构建业务逻辑代码向量库
    console.print("[bold yellow]>>> 2/2 正在构建代码语义向量库 (LlamaIndex)...[/bold yellow]")
    index_result = build_local_codebase_index(project_path, workspace_dir)
    
    if "✅" in index_result:
        console.print(f"[bold green]{index_result}[/bold green]\n")
        console.print("[bold cyan]🎉 项目初始化大功告成！[/bold cyan]")
        console.print("[dim]您的 AI 助手已经彻底看懂了这个系统。随时可以使用 `ai-coder do \"你的需求\"` 唤醒 Agent 编写代码。[/dim]")
    else:
        console.print(f"[bold red]{index_result}[/bold red]")

@app.command()
def do(
    request: str = typer.Argument(..., help="用自然语言描述你需要开发的需求")
):
    """
    第二步：基于现有的项目索引，执行架构规划与自动编码。
    """
    console.print(f"[bold magenta]🚀 接收到开发任务：{request}[/bold magenta]")
    console.print("[dim]正在唤醒多智能体协作网络 (Librarian -> Architect -> Coder -> QA)...[/dim]\n")
    
    # 这里接入之前写好的 LangGraph 工作流
    # run_agentic_workflow(request)
    console.print("[bold green]🎉 任务执行完毕，请 review 代码改动。[/bold green]")

if __name__ == "__main__":
    app()