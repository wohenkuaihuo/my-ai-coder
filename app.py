import glob
import os
import re

import streamlit as st

from config_manager import (
    CHAT_PROFILE_TYPE,
    EMBEDDING_PROFILE_TYPE,
    ConfigError,
    create_openai_client,
    get_active_chat_config,
    get_active_chat_model,
    get_active_embedding_config,
    get_profiles,
    get_project_workspace_dir,
    load_app_config,
    save_app_config,
    validate_model_config,
)
from indexer import build_local_codebase_index
from memory_manager import append_chat_message, load_chat_memory
from pom_parser import extract_pom_info, generate_tech_stack_report
from workflow import agent_app

from config_manager import create_openai_client, get_active_chat_model

# ================= 页面全局配置 =================
st.set_page_config(
    page_title="AI 架构师工作台 | AI Coder Studio",
    page_icon="🤖",
    layout="wide"
)

def get_workspace_dir(target_path):
    """根据目标项目路径生成唯一的本地工作区目录"""
    return get_project_workspace_dir(target_path)


def load_config_to_session():
    if "app_config" not in st.session_state:
        st.session_state["app_config"] = load_app_config()


def persist_config(config):
    st.session_state["app_config"] = save_app_config(config)


def profile_keys(profile_type):
    if profile_type == CHAT_PROFILE_TYPE:
        return "chat_profiles", "active_chat_profile"
    return "embedding_profiles", "active_embedding_profile"


def mask_secret(value):
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def model_group_label(profile_type):
    return "Chat模型" if profile_type == CHAT_PROFILE_TYPE else "Embedding模型"


def is_project_cached(ws_dir):
    """检测工作区是否已经存在完整的初始化缓存"""
    tech_stack_exists = os.path.exists(os.path.join(ws_dir, "01_Tech_Stack.md"))
    domain_model_exists = os.path.exists(os.path.join(ws_dir, "02_Domain_Models.md"))
    vector_store_exists = os.path.exists(os.path.join(ws_dir, "vector_store"))
    
    # 只有三大件全部存在，才认为缓存有效
    return tech_stack_exists and domain_model_exists and vector_store_exists

def run_full_initialization(target_project_path, ws_dir):
    """提取出来的核心初始化引擎（包含全套耗时操作）"""
    with st.status("正在执行深度扫描与重新注入...", expanded=True) as status:
        # 1. 技术栈解析
        st.write("🔍 1/3 正在深入提取 Maven 依赖拓扑...")
        pom_data = extract_pom_info(os.path.join(target_project_path, "pom.xml"))
        if isinstance(pom_data, dict):
            st.write("🧠 2/3 正在生成《技术栈评估报告》...")
            generate_tech_stack_report(pom_data, ws_dir) 
            st.write("   -> ✅ 底层技术栈解析完毕。")
        else:
            st.error(f"⚠️ 解析 pom.xml 出现异常: {pom_data}")
        
        # 2. 领域模型反推
        st.write("🔍 2.5/3 正在扫描项目 SQL，反推核心业务领域模型...")
        sql_files = glob.glob(os.path.join(target_project_path, '**/*.sql'), recursive=True)
        if sql_files:
            sql_content = ""
            for f_path in sql_files:
                with open(f_path, 'r', encoding='utf-8') as f:
                    sql_content += f"\n--- {os.path.basename(f_path)} ---\n" + f.read()[:2000]
            
            st.write("🧠 正在生成《核心领域模型白皮书》...")
            client = create_openai_client() # ⚠️ 记得换成你的 Key
            chat_model = get_active_chat_model()
            prompt = """你是一位资深的业务架构师。请阅读以下项目的数据库 DDL 脚本片段。
            请你输出一份 Markdown 格式的《核心领域模型白皮书》，包含：
            1. 项目宏观业务定位。
            2. 核心实体梳理。
            3. 核心业务流转过程猜测。"""
            
            response = client.chat.completions.create(
                model=chat_model, 
                messages=[{"role": "user", "content": prompt + "\n\n" + sql_content[:15000]}] 
            )
            with open(os.path.join(ws_dir, "02_Domain_Models.md"), "w", encoding="utf-8") as f:
                f.write(response.choices[0].message.content)
            st.write("   -> ✅ 业务领域拓扑文档已生成。")
        else:
            st.write("   -> ⚠️ 未检测到 SQL 文件，跳过业务模型反推。")

        # 3. 向量库构建
        st.write("📚 3/3 正在构建 LlamaIndex 本地语义向量库...")
        index_result = build_local_codebase_index(target_project_path, ws_dir) 
        
        if "✅" in index_result:
            status.update(label="项目注入完成！", state="complete", expanded=False)
            st.session_state['is_initialized'] = True
            st.session_state['current_workspace'] = ws_dir 
            st.success(f"🎉 成功挂载项目: {os.path.basename(target_project_path)}")
        else:
            status.update(label="知识库构建失败", state="error", expanded=False)
            st.error(index_result)

def collect_model_profile_rows():
    config = st.session_state["app_config"]
    rows = []
    for profile_type in (CHAT_PROFILE_TYPE, EMBEDDING_PROFILE_TYPE):
        list_key, active_key = profile_keys(profile_type)
        for index, profile in enumerate(config[list_key]):
            rows.append({
                "id": f"{profile_type}:{index}",
                "profile_type": profile_type,
                "index": index,
                "profile": profile,
                "is_active": profile["name"] == config[active_key],
                "group": model_group_label(profile_type),
            })
    return rows


def set_model_form(mode, profile_type=None, index=None):
    st.session_state["model_form_mode"] = mode
    st.session_state["model_form_profile_type"] = profile_type
    st.session_state["model_form_index"] = index


def reset_model_filters():
    st.session_state["model_search_keyword"] = ""
    st.session_state["model_group_filter"] = "全部分组"


def render_model_config_toolbar():
    cols = st.columns([2, 1.5, 0.7, 0.7, 1])
    with cols[0]:
        st.text_input("搜索关键词", key="model_search_keyword", placeholder="搜索名称、模型、Base URL")
    with cols[1]:
        st.selectbox("全部分组", ["全部分组", "Chat模型", "Embedding模型"], key="model_group_filter")
    with cols[2]:
        st.write("")
        st.button("查询", use_container_width=True)
    with cols[3]:
        st.write("")
        st.button("重置", use_container_width=True, on_click=reset_model_filters)
    with cols[4]:
        st.write("")
        if st.button("新增配置", use_container_width=True, type="primary"):
            set_model_form("create", CHAT_PROFILE_TYPE, None)
            st.rerun()


def filter_model_rows(rows):
    keyword = st.session_state.get("model_search_keyword", "").strip().lower()
    group_filter = st.session_state.get("model_group_filter", "全部分组")
    filtered_rows = []
    for row in rows:
        profile = row["profile"]
        searchable_text = " ".join([
            profile.get("name", ""),
            profile.get("base_url", ""),
            profile.get("model", ""),
            row["group"],
        ]).lower()
        if keyword and keyword not in searchable_text:
            continue
        if group_filter != "全部分组" and row["group"] != group_filter:
            continue
        filtered_rows.append(row)
    return filtered_rows


def render_model_config_table(rows):
    header_cols = st.columns([0.4, 1.5, 1, 1.1, 1.4, 1.2, 1, 1.2, 1.2, 1.8])
    headers = ["", "名称", "状态", "分组", "密钥", "可用模型", "IP限制", "创建时间", "过期时间", "操作"]
    for col, header in zip(header_cols, headers):
        col.markdown(f"**{header}**")

    st.divider()
    if not rows:
        st.info("暂无匹配的模型配置")
        return

    for row in rows:
        profile = row["profile"]
        row_cols = st.columns([0.4, 1.5, 1, 1.1, 1.4, 1.2, 1, 1.2, 1.2, 1.8])
        row_cols[0].checkbox("", value=False, key=f"select_{row['id']}")
        row_cols[1].markdown(f"**{profile.get('name') or '未命名'}**")
        status_text = "🟢 已启用" if row["is_active"] else "⚪ 未启用"
        row_cols[2].markdown(status_text)
        row_cols[3].markdown(row["group"])
        row_cols[4].markdown(f"`{mask_secret(profile.get('api_key'))}`")
        row_cols[5].markdown(profile.get("model") or "未配置")
        row_cols[6].markdown("无限制")
        row_cols[7].markdown(profile.get("created_at") or "-")
        row_cols[8].markdown(profile.get("expires_at") or "永久不过期")

        action_cols = row_cols[9].columns([1, 1, 1])
        if action_cols[0].button("启用", key=f"activate_{row['id']}", disabled=row["is_active"]):
            _, active_key = profile_keys(row["profile_type"])
            st.session_state["app_config"][active_key] = profile["name"]
            persist_config(st.session_state["app_config"])
            st.rerun()
        if action_cols[1].button("编辑", key=f"edit_{row['id']}"):
            set_model_form("edit", row["profile_type"], row["index"])
            st.rerun()
        if action_cols[2].button("删除", key=f"delete_{row['id']}"):
            list_key, active_key = profile_keys(row["profile_type"])
            profiles = st.session_state["app_config"][list_key]
            if len(profiles) <= 1:
                st.warning("至少需要保留一个配置")
            else:
                deleted_name = profile["name"]
                profiles.pop(row["index"])
                if st.session_state["app_config"][active_key] == deleted_name:
                    st.session_state["app_config"][active_key] = profiles[0]["name"]
                persist_config(st.session_state["app_config"])
                st.rerun()
        st.divider()


def render_model_config_form():
    mode = st.session_state.get("model_form_mode")
    if mode not in {"create", "edit"}:
        return

    profile_type = st.session_state.get("model_form_profile_type") or CHAT_PROFILE_TYPE
    list_key, active_key = profile_keys(profile_type)
    profile_index = st.session_state.get("model_form_index")
    is_edit = mode == "edit" and profile_index is not None
    current_profile = (
        st.session_state["app_config"][list_key][profile_index]
        if is_edit else {"name": "", "api_key": "", "base_url": "", "model": ""}
    )
    title = "编辑模型配置" if is_edit else "新增模型配置"

    st.subheader(title)
    with st.form("model_config_form"):
        selected_group = st.selectbox(
            "分组",
            [CHAT_PROFILE_TYPE, EMBEDDING_PROFILE_TYPE],
            index=0 if profile_type == CHAT_PROFILE_TYPE else 1,
            format_func=model_group_label,
            disabled=is_edit,
        )
        target_list_key, target_active_key = profile_keys(selected_group)
        name = st.text_input("名称", value=current_profile.get("name", ""))
        api_key = st.text_input("密钥", value=current_profile.get("api_key", ""), type="password")
        base_url = st.text_input("Base URL", value=current_profile.get("base_url", ""))
        model = st.text_input("模型名称", value=current_profile.get("model", ""))
        save_col, cancel_col = st.columns([1, 5])
        submitted = save_col.form_submit_button("保存")
        cancelled = cancel_col.form_submit_button("取消")

        if cancelled:
            set_model_form(None, None, None)
            st.rerun()

        if submitted:
            clean_name = name.strip()
            profiles = st.session_state["app_config"][target_list_key]
            existed_names = [item["name"] for idx, item in enumerate(profiles) if not (is_edit and idx == profile_index)]
            if not clean_name:
                st.error("名称不能为空")
                return
            if clean_name in existed_names:
                st.error("同一分组下名称不能重复")
                return

            new_profile = {
                "name": clean_name,
                "api_key": api_key.strip(),
                "base_url": base_url.strip(),
                "model": model.strip(),
            }
            if is_edit:
                old_name = profiles[profile_index]["name"]
                profiles[profile_index] = new_profile
                if st.session_state["app_config"][active_key] == old_name:
                    st.session_state["app_config"][active_key] = clean_name
            else:
                profiles.append(new_profile)
                st.session_state["app_config"][target_active_key] = clean_name

            persist_config(st.session_state["app_config"])
            set_model_form(None, None, None)
            st.success("模型配置已保存")
            st.rerun()


def render_model_config_page():
    top_cols = st.columns([6, 1])
    with top_cols[0]:
        st.header("模型配置")
        st.caption("集中管理大模型 Chat 与 Embedding 参数。")
    with top_cols[1]:
        if st.button("返回", use_container_width=True):
            st.session_state["active_page"] = "chat"
            st.rerun()

    render_model_config_toolbar()
    rows = filter_model_rows(collect_model_profile_rows())
    render_model_config_table(rows)
    render_model_config_form()


def validate_runtime_config(require_embedding=True):
    try:
        config = st.session_state["app_config"]
        validate_model_config(get_active_chat_config(config), "Chat 模型")
        if require_embedding:
            validate_model_config(get_active_embedding_config(config), "Embedding 模型")
        return True
    except ConfigError as e:
        st.error(str(e))
        return False


load_config_to_session()

# ================= 侧边栏：项目挂载与初始化 =================
with st.sidebar:
    st.image("https://api.dicebear.com/7.x/bottts/svg?seed=Arch&backgroundColor=1e1e1e", width=80)
    st.title("工作区配置")
    st.caption("旁路挂载模式，零代码侵入")
    
    st.divider()
    if st.button("模型配置", use_container_width=True):
        st.session_state["active_page"] = "model_config"
    if st.button("返回聊天", use_container_width=True):
        st.session_state["active_page"] = "chat"
    st.divider()
    
    # 1. 可视化选择/输入目标项目路径
    target_project_path = st.text_input(
        "📁 目标项目绝对路径", 
        value=st.session_state.get('project_path', ''),
        placeholder="例如: D:\\work\\plaza-cleaning"
    )
    
    # 保存路径到 session_state
    # 动态检测与按钮渲染逻辑
    if target_project_path:
        st.session_state['project_path'] = target_project_path
        
        if not os.path.exists(target_project_path):
            st.error("❌ 目标路径不存在于本机，请检查输入！")
        else:
            # 获取统一的工作区目录
            ws_dir = get_workspace_dir(target_project_path)
            
            # 检测是否命中缓存
            if is_project_cached(ws_dir):
                st.success("📦 已检测到该项目的上下文缓存！")
                
                # 使用两列并排展示两个按钮
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("⚡ 极速挂载", use_container_width=True, type="primary"):
                        st.session_state['is_initialized'] = True
                        st.session_state['current_workspace'] = ws_dir
                        st.toast("极速挂载成功，随时可以开始聊天！", icon="🚀")
                with col2:
                    if st.button("🔄 强制重载", use_container_width=True):
                        # 用户可能刚拉取了最新代码，选择强制重新分析
                        run_full_initialization(target_project_path, ws_dir)
            else:
                st.info("💡 这是一个新项目，需要进行首次分析架构。")
                if st.button("🚀 首次扫描并注入", use_container_width=True, type="primary"):
                    run_full_initialization(target_project_path, ws_dir)

    # # 3. 状态指示灯
    # st.divider()
    # st.write("📊 **当前挂载状态**")
    # if st.session_state.get('is_initialized'):
    #     st.info(f"🟢 已连接知识库: {os.path.basename(st.session_state.get('project_path', ''))}")
    # else:
    #     st.warning("🔴 未连接 (请先执行挂载)")
    
    # 2. 一键初始化按钮 (等价于 CLI 中的 ai-coder init)
    # 2. 一键初始化按钮
    if st.button("🚀 扫描并注入新项目", use_container_width=True, type="primary"):
        if not os.path.exists(target_project_path):
            st.error("路径不存在，请检查！")
        elif not validate_runtime_config(require_embedding=True):
            st.stop()
        else:
            # 获取统一的工作区目录
            ws_dir = get_workspace_dir(target_project_path)
            
            with st.status("正在执行【洞察模式】初始化...", expanded=True) as status:
                
                # ------ 步骤 1：技术栈雷达 ------
                st.write("🔍 1/3 正在深入提取 Maven 依赖拓扑...")
                pom_data = extract_pom_info(os.path.join(target_project_path, "pom.xml"))
                
                if isinstance(pom_data, dict):
                    st.write("🧠 2/3 正在请 AI 架构师分析并生成《技术栈评估报告》...")
                    # 【核心修复点 👇】：传入 ws_dir 作为第二个参数
                    generate_tech_stack_report(pom_data, ws_dir) 
                    st.write("   -> ✅ 底层技术栈解析完毕。")
                else:
                    st.error(f"⚠️ 解析 pom.xml 出现异常: {pom_data}")
                
                # ------ 步骤 2：业务模型反推 ------
                st.write("🔍 2.5/3 正在扫描项目 SQL，反推核心业务领域模型...")
                sql_files = glob.glob(os.path.join(target_project_path, '**/*.sql'), recursive=True)
                
                if sql_files:
                    sql_content = ""
                    for f_path in sql_files:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            sql_content += f"\n--- {os.path.basename(f_path)} ---\n" + f.read()[:2000]
                    
                    st.write("🧠 正在请 AI 架构师绘制《核心领域模型白皮书》...")
                    
                    client = create_openai_client(st.session_state["app_config"])
                    chat_model = get_active_chat_model(st.session_state["app_config"])
                    
                    prompt = """你是一位资深的业务架构师。请阅读以下项目的数据库 DDL 脚本片段。
                    请你输出一份 Markdown 格式的《核心领域模型白皮书》，包含：
                    1. 项目宏观业务定位。
                    2. 核心实体梳理（最重要的 3-5 张表及业务含义）。
                    3. 核心业务流转过程猜测。"""
                    
                    response = client.chat.completions.create(
                        model=chat_model,
                        messages=[{"role": "user", "content": prompt + "\n\n" + sql_content[:15000]}] 
                    )
                    
                    # 【路径修复点 👇】：直接写进统一的工作区目录
                    with open(os.path.join(ws_dir, "02_Domain_Models.md"), "w", encoding="utf-8") as f:
                        f.write(response.choices[0].message.content)
                    st.write("   -> ✅ 业务领域拓扑文档已生成。")
                else:
                    st.write("   -> ⚠️ 未检测到 SQL 文件，跳过业务模型反推。")

                # ------ 步骤 3：构建向量库 ------
                st.write("📚 3/3 正在切分 Java/SQL 文件，构建 LlamaIndex 本地语义向量库...")
                # 【参数修复点 👇】：同样把 ws_dir 传给向量建库函数
                index_result = build_local_codebase_index(target_project_path, ws_dir) 
                
                if "✅" in index_result:
                    status.update(label="项目注入完成！全局上下文已就绪。", state="complete", expanded=False)
                    st.session_state['is_initialized'] = True
                    # 顺便把工作区路径存进 session，方便后续读取报告展示
                    st.session_state['current_workspace'] = ws_dir 
                    st.success(f"🎉 成功洞察并挂载项目: {os.path.basename(target_project_path)}")
                else:
                    status.update(label="知识库构建失败", state="error", expanded=False)
                    st.error(index_result)
    # 3. 状态指示灯
    st.divider()
    st.write("📊 **当前挂载状态**")
    if st.session_state.get('is_initialized'):
        st.info("🟢 知识库已连接 (LlamaIndex)")
    else:
        st.warning("🔴 未连接 (请先执行扫描)")

# ================= 主界面：Agent 交互流 =================
if st.session_state.get("active_page") == "model_config":
    render_model_config_page()
    st.stop()

# ================= 文档导航与搜索增强 =================

def highlight_text(text, keyword):
    """大小写不敏感的关键词高亮，返回带 <mark> 标签的 HTML。"""
    if not keyword:
        return text
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    return pattern.sub(
        lambda m: f'<mark style="background:#fff3cd;padding:1px 4px;border-radius:3px;">{m.group(0)}</mark>',
        text,
    )


def render_md_with_nav(md_content, tab_key):
    """
    增强型 Markdown 渲染，支持：
    - 自动提取 ## / ### 标题生成目录（ToC）
    - st.selectbox 章节下拉导航
    - 选择章节后聚焦显示，保留上下文
    """
    # 解析所有二级/三级标题
    headings = re.findall(r'^(#{2,4})\s+(.+)$', md_content, re.MULTILINE)

    # 内容太短（少于 3 个章节），直接渲染
    if len(headings) < 3:
        st.markdown(md_content)
        return

    # 按标题切分文档为章节列表
    lines = md_content.split('\n')
    sections = []  # [(level, title, text_lines, start_line_idx)]
    current_section = None

    for i, line in enumerate(lines):
        m = re.match(r'^(#{2,4})\s+(.+)$', line)
        if m:
            if current_section:
                sections.append(current_section)
            current_section = [len(m.group(1)), m.group(2), [line], i]
        elif current_section:
            current_section[2].append(line)
    if current_section:
        sections.append(current_section)

    # ── 章节导航栏 ──
    nav_options = ["📋 查看完整文档"] + \
                  [f"{'  ' * (s[0] - 2)}{s[1]}" for s in sections]
    selected = st.selectbox(
        "📖 跳转章节", nav_options,
        key=f"doc_nav_{tab_key}",
    )

    # ── 生成目录（ToC）─以锚点链接形式 ──
    toc_lines = ["### 📑 目录\n"]
    for i, (level, title, _, _) in enumerate(sections):
        indent = "  " * (level - 2)
        toc_lines.append(f"{indent}- [{title}](#sec-{tab_key}-{i})")
    toc_lines.append("\n---\n")
    toc = "\n".join(toc_lines)

    # ── 在原始文档每个标题前注入命名锚点 ──
    anchored_lines = []
    sec_idx = 0
    for line in lines:
        m = re.match(r'^(#{2,4})\s+(.+)$', line)
        if m and sec_idx < len(sections):
            anchored_lines.append(f'<a id="sec-{tab_key}-{sec_idx}"></a>')
            sec_idx += 1
        anchored_lines.append(line)
    anchored_content = '\n'.join(anchored_lines)

    # ── 根据导航选择决定展示内容 ──
    if selected == "📋 查看完整文档":
        display_content = anchored_content
        focus_title = None
    else:
        # 找到被选中的章节
        clean_title = selected.strip()
        focus_idx = next(
            (i for i, (_, t, _, _) in enumerate(sections) if t == clean_title),
            None
        )
        if focus_idx is not None:
            # 从选中章节开始往后展示（保留后续上下文的连贯性）
            start_line = sections[focus_idx][3]
            display_lines = anchored_lines[start_line:]
            display_content = '\n'.join(display_lines)
            focus_title = sections[focus_idx][1]
        else:
            display_content = anchored_content
            focus_title = None

    # ── 渲染 ──
    if focus_title:
        st.info(f"🔍 聚焦章节：**{focus_title}**（上方目录可点击跳转，或选择「查看完整文档」返回）")

    st.markdown(toc + display_content, unsafe_allow_html=True)


def render_searchable_md(md_content, tab_key):
    """
    增强型 MD 渲染器，融合搜索 + 导航两种模式。

    - 有搜索词 → 搜索模式：按章节聚合展示匹配结果，关键词高亮
    - 无搜索词 → 导航阅读模式：目录锚点 + 章节跳转
    """
    search = st.text_input(
        "🔍", "",
        placeholder="输入关键词搜索当前文档...",
        key=f"doc_search_{tab_key}",
        label_visibility="collapsed",
    )

    if not search:
        # ── 无搜索词：常规导航阅读模式 ──
        render_md_with_nav(md_content, tab_key)
        return

    # ── 搜索模式 ──
    lines = md_content.split('\n')

    # 按标题切分为章节
    sections = []
    current_title = ("##", "文档开头")
    current_start = 0

    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,4})\s+(.+)$', line)
        if m:
            if current_start < i:
                sections.append((*current_title, current_start, i))
            current_title = (m.group(1), m.group(2))
            current_start = i
    sections.append((*current_title, current_start, len(lines)))

    # 找出有匹配的章节
    matched_sections = []
    total_matches = 0
    kw_lower = search.lower()
    for level, title, start, end in sections:
        section_text = '\n'.join(lines[start:end])
        count = section_text.lower().count(kw_lower)
        if count:
            total_matches += count
            matched_sections.append((level, title, start, end, count))

    if not matched_sections:
        st.warning(f'❌ 未找到包含「{search}」的内容')
        render_md_with_nav(md_content, tab_key)
        return

    # ── 渲染搜索结果 ──
    st.info(f"✅ 在 **{len(matched_sections)}** 个章节中找到 **{total_matches}** 处匹配")

    for level, title, start, end, count in matched_sections:
        indent = "　" if level.count('#') >= 3 else ""
        label = f"{indent}📄 {title}（{count}处匹配）"
        with st.expander(label, expanded=len(matched_sections) <= 5):
            content = '\n'.join(lines[start:end])
            highlighted = highlight_text(content, search)
            st.markdown(
                '<div style="border-left:3px solid #ff9800;padding:6px 0 6px 16px;">'
                f'{highlighted}</div>',
                unsafe_allow_html=True,
            )


def read_md_safe(ws_dir, filename):
    """安全读取工作区中的 Markdown 文件。"""
    filepath = os.path.join(ws_dir, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    return f"*（未生成 {filename}，可能扫描时跳过或发生了异常）*"


@st.dialog("📊 项目全景洞察报告", width="large")
def open_report_dialog(ws_dir):
    """弹窗展示项目全景洞察报告，支持搜索与导航。"""
    tab1, tab2, tab3 = st.tabs(["⚙️ 技术栈评估", "🏛️ 领域模型白皮书", "🗂️ 向量索引报告"])

    with tab1:
        render_searchable_md(read_md_safe(ws_dir, "01_Tech_Stack.md"), "tech_stack")
    with tab2:
        render_searchable_md(read_md_safe(ws_dir, "02_Domain_Models.md"), "domain_model")
    with tab3:
        render_searchable_md(read_md_safe(ws_dir, "03_Index_Report.md"), "index_report")


# ================= 主界面：项目洞察与 Agent 交互 =================
st.header("💬 AI 架构师工作台")

if st.session_state.get('is_initialized'):
    ws_dir = st.session_state.get('current_workspace')

    # 项目报告入口：醒目的深色卡片
    st.markdown(
        """
        <style>
        .report-hero {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-radius: 14px;
            padding: 20px 28px;
            margin: 12px 0 20px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 4px 24px rgba(0,0,0,0.20);
        }
        .report-hero-left {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .report-hero-icon {
            font-size: 32px;
        }
        .report-hero-title {
            font-size: 20px;
            font-weight: 700;
            color: #f1f5f9;
            line-height: 1.3;
        }
        .report-hero-sub {
            font-size: 13px;
            color: #94a3b8;
            margin-top: 2px;
        }
        </style>
        <div class="report-hero">
            <div class="report-hero-left">
                <span class="report-hero-icon">📊</span>
                <div>
                    <div class="report-hero-title">项目全景洞察报告</div>
                    <div class="report-hero-sub">技术栈评估 · 领域模型白皮书 · 语义索引报告</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("📂 打开全景报告", use_container_width=True, type="primary"):
        open_report_dialog(ws_dir)

# 初始化聊天记录
if "messages" not in st.session_state:
    st.session_state.messages = []

# 渲染历史聊天记录
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


import uuid
# 导入我们刚在 workflow.py 中抛出的支持断点的引擎
from workflow import agent_app 

# ================= 会话状态初始化 =================
# 为当前对话生成一个唯一的线程 ID（LangGraph 恢复断点必须依赖此 ID）
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

# 定义 LangGraph 运行的配置
thread_config = {"configurable": {"thread_id": st.session_state.thread_id}}

# ================= 聊天流处理器 =================
def process_agent_stream(stream_input=None):
    """负责驱动 Agent 引擎运转，并实时渲染状态指示器"""
    status_box = st.status("🚀 AI 研发团队协作中...", expanded=True)
    
    try:
        # 如果 stream_input 为 None，意味着是从断点处“恢复执行”
        for output in agent_app.stream(stream_input, config=thread_config):
            for node_name, step_state in output.items():
                if node_name == "Librarian":
                    status_box.write("📚 [Librarian] 已完成本地上下文的深度语义检索。")
                
                elif node_name == "Architect":
                    status_box.write("📐 [Architect] 架构设计完毕。")
                    plan_content = step_state.get("execution_plan", "")
                    
                    # 架构出图后，立即渲染到聊天流中供用户阅读
                    msg = f"### 📝 【架构师出图】最新执行计划\n\n{plan_content}"
                    st.session_state.messages.append({"role": "assistant", "content": msg})
                    st.markdown(msg)
                    
                elif node_name == "Coder":
                    retry = step_state.get('retry_count', 1)
                    status_box.write(f"💻 [Coder] 正在编写代码并写入目标磁盘... (尝试次数: {retry})")
                    
                elif node_name == "QA":
                    errors = step_state.get("qa_errors", [])
                    if errors:
                        status_box.error(f"🧪 [QA] 编译拦截！发现错误，正在自动打回重写...")
                    else:
                        status_box.success("🧪 [QA] 编译测试完美通过！")
                        
        status_box.update(label="当前阶段流转完毕", state="complete", expanded=False)
        
    except Exception as e:
        status_box.update(label="流转异常", state="error", expanded=False)
        st.error(f"引擎异常: {str(e)}")


# ================= 核心流转与 UI 渲染逻辑 =================

# 1. 向引擎查询当前图的流转状态
current_state = agent_app.get_state(thread_config)
# 判断引擎是否被我们设定的 interrupt_before=["Human"] 挂起了
is_waiting_for_human = current_state.next and "Human" in current_state.next

if is_waiting_for_human:
    # --- 挂起态 UI：渲染审批表单 ---
    st.warning("✋ 流程已暂停，等待您的最终拍板：")
    
    with st.form("human_review_form"):
        feedback = st.text_area("架构师修改意见 (如同意计划，可直接留空或写'OK')：")
        col1, col2 = st.columns(2)
        with col1:
            approve_btn = st.form_submit_button("✅ 同意执行计划，开始写代码！", type="primary")
        with col2:
            reject_btn = st.form_submit_button("❌ 打回重新规划")
        
        if approve_btn or reject_btn:
            status = "approved" if approve_btn else "rejected"
            
            # 【关键动作 1】：将用户的意图强制注入到 LangGraph 的黑板 (State) 中
            agent_app.update_state(
                thread_config, 
                {"approval_status": status, "human_feedback": feedback}
            )
            
            # 记录历史
            log_msg = f"【人类审查】已{'✅ 放行' if approve_btn else '❌ 打回'}。意见：{feedback}"
            st.session_state.messages.append({"role": "user", "content": log_msg})
            
            # 【关键动作 2】：立刻唤醒挂起的引擎，从断点继续向下流转
            with st.chat_message("assistant"):
                process_agent_stream(None) # 传 None 唤醒

            # 流程走完，刷新网页，消除表单并展示新状态
            st.rerun()

else:
    # --- 常规态 UI：渲染聊天输入框 ---
    if prompt := st.chat_input("描述您的开发需求..."):

        # 拦截：确保必须先挂载项目
        if not st.session_state.get('is_initialized'):
            st.error("请先在左侧侧边栏初始化项目！")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            # 组装初始启动数据，发车！
            initial_state = {
                "user_request": prompt,
                "target_project_path": st.session_state['project_path'],
                "retry_count": 0,
                "qa_errors": []
            }
            process_agent_stream(initial_state)

        # 第一次流转执行到挂起点后，强制刷新网页触发上方表单渲染
        st.rerun()
