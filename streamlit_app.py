import json
import re
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, db

# ==========================================
# CẤU HÌNH TRANG
# ==========================================
st.set_page_config(page_title="Issue Follow", page_icon="🔧", layout="wide")


# ==========================================
# ĐĂNG NHẬP (PASSWORD GATE)
# ==========================================
def check_password():
    def password_entered():
        if st.session_state.get("password_input") == st.secrets.get("app_password"):
            st.session_state["authenticated"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["authenticated"] = False

    if st.session_state.get("authenticated"):
        return True

    st.title("🔧 Issue Follow")
    st.text_input("Mật khẩu truy cập", type="password", key="password_input", on_change=password_entered)
    if st.session_state.get("authenticated") is False:
        st.error("Sai mật khẩu, thử lại.")
    return False


# ==========================================
# KẾT NỐI FIREBASE (dùng chung project với app TKB_TODO)
# "issue_follow" là node cùng cấp với "tasks" trong cùng 1 Realtime Database.
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        missing = [k for k in ("app_password", "firebase_database_url", "firebase_service_account") if k not in st.secrets]
        if missing:
            st.error(
                "⚠️ Thiếu cấu hình Secrets trên Streamlit Cloud: **" + ", ".join(missing) + "**\n\n"
                "Vào Settings → Secrets của app, dán đúng theo mẫu `secrets.toml.example`. "
                "Lưu ý: `app_password` và `firebase_database_url` phải nằm **TRƯỚC** dòng "
                "`[firebase_service_account]` — nếu đặt sau, TOML sẽ hiểu nhầm 2 dòng đó "
                "thuộc bên trong bảng `firebase_service_account` và app sẽ không tìm thấy."
            )
            st.stop()
        cred = credentials.Certificate(dict(st.secrets["firebase_service_account"]))
        firebase_admin.initialize_app(cred, {"databaseURL": st.secrets["firebase_database_url"]})
    return True


# ==========================================
# TRUY XUẤT DỮ LIỆU (Customers -> Issues -> Steps)
# Customer và Issue đều dùng ID nội bộ ngẫu nhiên làm key (KHÔNG dùng tên làm key)
# để tránh lỗi ghi đè khi trùng tên, và tránh ký tự cấm của Firebase key (. $ # [ ] /).
# ==========================================
def customers_ref():
    return db.reference("issue_follow/customers")


def tasks_ref():
    return db.reference("tasks")  # node dùng chung với TKB_TODO


def load_all_customers():
    data = customers_ref().get()
    return data or {}


def save_new_customer(name):
    new_id = str(uuid.uuid4())[:8]
    customers_ref().child(new_id).set({"name": name, "issues": {}})
    return new_id


def save_new_issue(customer_id, title, device, serial, url):
    new_id = str(uuid.uuid4())[:8]
    customers_ref().child(customer_id).child("issues").child(new_id).set({
        "title": title,
        "device": device.strip() if device else "N/A",
        "serial": serial.strip() if serial else "N/A",
        "status": "Pending",
        "url": url.strip() if url else "",
        "steps": {},
    })
    return new_id


def update_issue(customer_id, issue_id, title, device, serial, status, url):
    customers_ref().child(customer_id).child("issues").child(issue_id).update({
        "title": title,
        "device": device.strip() if device else "N/A",
        "serial": serial.strip() if serial else "N/A",
        "status": status,
        "url": url.strip() if url else "",
    })


def add_activity(customer_id, issue_id, activity, act_date, pic, result, lead_time, close_issue):
    step_id = str(uuid.uuid4())[:8]
    customers_ref().child(customer_id).child("issues").child(issue_id).child("steps").child(step_id).set({
        "date": act_date,
        "activity": activity,
        "pic": pic.strip() if pic else "N/A",
        "result": result.strip() if result else "",
        "lead_time": lead_time or "",
    })
    if close_issue:
        customers_ref().child(customer_id).child("issues").child(issue_id).child("status").set("Fixed")


def update_activity(customer_id, issue_id, step_id, activity, act_date, pic, result, lead_time, close_issue):
    customers_ref().child(customer_id).child("issues").child(issue_id).child("steps").child(step_id).update({
        "date": act_date,
        "activity": activity,
        "pic": pic.strip() if pic else "N/A",
        "result": result.strip() if result else "",
        "lead_time": lead_time or "",
    })
    customers_ref().child(customer_id).child("issues").child(issue_id).child("status").set(
        "Fixed" if close_issue else "Pending"
    )


def delete_activity(customer_id, issue_id, step_id):
    customers_ref().child(customer_id).child("issues").child(issue_id).child("steps").child(step_id).delete()


# ==========================================
# GỢI Ý / TIỆN ÍCH DÙNG CHUNG (giữ nguyên logic từ bản Tkinter)
# ==========================================
def extract_pic_display_name(pic_str):
    """
    Chuẩn hoá PIC, trả về TÊN THUẦN (không ':' không ngoặc).
    Không loại trừ Nick/Nick Lai nữa - tất cả PIC đều được giữ nguyên,
    vì mọi task giờ đều chuyển hết sang app TKB_TODO.
    """
    if not pic_str:
        return ""
    pic_str = pic_str.strip()
    if not pic_str or pic_str.lower() == "n/a":
        return ""
    parts = re.split(r"[,;&]|\s+và\s+|\s+and\s+", pic_str, flags=re.IGNORECASE)
    remaining = [p.strip() for p in parts if p.strip()]
    return ", ".join(remaining)


def get_all_device_names(all_customers):
    names = set()
    for cust in all_customers.values():
        for issue in (cust.get("issues") or {}).values():
            dev = (issue.get("device") or "").strip()
            if dev and dev != "N/A":
                names.add(dev)
    return sorted(names)


def find_matching_issue_titles(all_customers, typed):
    typed_lower = typed.strip().lower()
    if len(typed_lower) < 2:
        return {}
    seen = {}
    for cust in all_customers.values():
        for issue in (cust.get("issues") or {}).values():
            title = issue.get("title", "")
            if title and typed_lower in title.lower():
                seen[title] = seen.get(title, 0) + 1
    return seen


def get_issue_fix_history_text(all_customers, title):
    lines = []
    for cust in all_customers.values():
        for issue in (cust.get("issues") or {}).values():
            if issue.get("title", "") == title:
                lines.append(f"📌 Tại: {cust.get('name', '')}")
                for step in (issue.get("steps") or {}).values():
                    lines.append(f" -> {step.get('activity', '')}: {step.get('result', '')} (PIC: {step.get('pic', '')})")
                lines.append("-" * 40)
    return "\n".join(lines) if lines else "Không tìm thấy chi tiết."


def parse_date_safe(date_str):
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").date()
    except Exception:
        return None


def is_issue_overdue(issue):
    today = date.today()
    for step in (issue.get("steps") or {}).values():
        lt = step.get("lead_time", "")
        if lt:
            d = parse_date_safe(lt)
            if d and (d - today).days < 0:
                return True
    return False


def issue_counts(customer):
    issues = customer.get("issues") or {}
    pending = sum(1 for i in issues.values() if i.get("status", "Pending") == "Pending")
    fixed = sum(1 for i in issues.values() if i.get("status", "Pending") == "Fixed")
    overdue = any(is_issue_overdue(i) for i in issues.values())
    return pending, fixed, overdue


def export_activity_to_task(cust_name, issue_title, step):
    lead_time_val = (step.get("lead_time") or "").strip()
    if not lead_time_val:
        st.error("Activity này chưa có hạn phản hồi (Lead-time). Vui lòng bổ sung trước khi tạo Task!")
        return

    act_name = (step.get("activity") or "").strip()
    pic_name = extract_pic_display_name(step.get("pic", ""))
    pic_prefix = f"{pic_name}: " if pic_name else ""
    task_title = f"{pic_prefix}{act_name} cho case {issue_title} của khách hàng {cust_name}"

    existing = tasks_ref().get() or {}
    for t in existing.values():
        if t.get("task") == task_title and t.get("lead") == lead_time_val:
            st.info("Task công việc này đã được xuất sang hệ thống TKB_TODO từ trước rồi!")
            return

    timestamp_id = str(int(datetime.now().timestamp() * 1000))
    tasks_ref().child(timestamp_id).set({
        "id": timestamp_id,
        "task": task_title,
        "pic": pic_name,
        "lead": lead_time_val,
        "original_due": lead_time_val,
        "history": [],
        "reason": "",
        "status": "pending",
        "event_sent": False,
    })
    st.success(f"✅ Đã tạo Task sang TKB_TODO:\n\n{task_title}")


# ==========================================
# GIAO DIỆN: TAB "QUẢN LÝ ISSUES"
# ==========================================
def render_sidebar(all_customers):
    st.sidebar.header("1. Khách Hàng")

    with st.sidebar.expander("🔍 Tìm / Thêm Khách Hàng"):
        typed = st.text_input("Nhập tên Khách hàng", key="cust_search_input")
        if typed:
            matches = {cid: c for cid, c in all_customers.items() if typed.lower() in c.get("name", "").lower()}
            exact_exists = any(c.get("name", "") == typed for c in all_customers.values())

            if matches:
                st.caption(f"Tìm thấy {len(matches)} khách hàng trùng khớp:")
                for cid, c in matches.items():
                    if st.button(f"→ {c.get('name')}", key=f"select_match_{cid}", use_container_width=True):
                        st.session_state.selected_customer_id = cid
                        st.session_state.selected_issue_id = None
                        st.rerun()
            else:
                st.caption("Không tìm thấy khách hàng nào trùng.")

            if not exact_exists:
                if st.button(f"+ Thêm Mới '{typed}'", key="add_new_cust_btn", type="primary", use_container_width=True):
                    new_id = save_new_customer(typed.strip())
                    st.session_state.selected_customer_id = new_id
                    st.session_state.selected_issue_id = None
                    st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Danh sách Khách Hàng ({len(all_customers)}):")
    for cid, c in sorted(all_customers.items(), key=lambda kv: kv[1].get("name", "")):
        pending, fixed, overdue = issue_counts(c)
        icon = "⚠️" if overdue else "📁"
        label = f"{icon} {c.get('name', '')}  (P:{pending}/F:{fixed})"
        is_selected = cid == st.session_state.get("selected_customer_id")
        if st.sidebar.button(label, key=f"cust_btn_{cid}", use_container_width=True,
                              type="primary" if is_selected else "secondary"):
            st.session_state.selected_customer_id = cid
            st.session_state.selected_issue_id = None
            st.rerun()


def render_add_issue_form(cid, all_customers, issues_empty):
    with st.expander("+ Thêm Issue mới", expanded=issues_empty):
        new_title = st.text_input("Tiêu đề sự cố (Issue Title)", key="new_issue_title")

        if new_title and len(new_title.strip()) > 1:
            matches = find_matching_issue_titles(all_customers, new_title)
            if matches:
                st.caption("🔎 Gợi ý Issue trùng trong hệ thống (click để dùng tên chuẩn):")
                for i, (title, count) in enumerate(sorted(matches.items(), key=lambda x: -x[1])):
                    label = f"🔹 {title}" + (f"  ({count} lần)" if count > 1 else "")
                    if st.button(label, key=f"suggest_{i}"):
                        st.session_state.new_issue_title = title
                        st.rerun()
                with st.expander("Xem cách fix cũ của các issue trùng"):
                    st.text(get_issue_fix_history_text(all_customers, new_title.strip()))
            else:
                st.caption("Chưa từng xảy ra lỗi trùng này. Đây là lỗi mới!")

        device_options = get_all_device_names(all_customers)
        dev_choice = st.selectbox("Tên Thiết Bị (Model)", options=["(Nhập thiết bị mới...)"] + device_options,
                                   key="new_issue_dev_choice")
        new_device = st.text_input("Nhập tên thiết bị mới", key="new_issue_dev_manual") \
            if dev_choice == "(Nhập thiết bị mới...)" else dev_choice

        new_serial = st.text_input("Số Serial (S/N)", key="new_issue_serial")
        new_url = st.text_input("Link tài liệu / SharePoint", key="new_issue_url")

        if st.button("💾 Lưu Issue", type="primary", key="save_new_issue_btn"):
            if new_title.strip():
                new_iid = save_new_issue(cid, new_title.strip(), new_device, new_serial, new_url)
                st.session_state.selected_issue_id = new_iid
                for k in ("new_issue_title", "new_issue_serial", "new_issue_url", "new_issue_dev_manual"):
                    st.session_state.pop(k, None)
                st.rerun()
            else:
                st.warning("Vui lòng nhập tiêu đề sự cố.")


def render_issue_list(cid, issues):
    st.markdown("##### Danh sách Issues")

    if st.session_state.get("selected_issue_id") not in issues:
        pending_ids = [iid for iid, i in issues.items() if i.get("status", "Pending") == "Pending"]
        st.session_state.selected_issue_id = pending_ids[0] if pending_ids else next(iter(issues))

    for iid, issue in issues.items():
        overdue = is_issue_overdue(issue)
        status = issue.get("status", "Pending")
        icon = "⚠️" if overdue else ("🔹" if status == "Pending" else "✅")
        label = f"{icon} {issue.get('title', '')} — {issue.get('device', 'N/A')} [{status}]"
        is_selected = iid == st.session_state.selected_issue_id
        if st.button(label, key=f"issue_btn_{iid}", use_container_width=True,
                     type="primary" if is_selected else "secondary"):
            st.session_state.selected_issue_id = iid
            st.rerun()


def render_edit_issue_form(cid, iid, issue, all_customers):
    with st.expander(f"✏️ Chỉnh sửa thông tin Issue"):
        e_title = st.text_input("Tiêu đề", value=issue.get("title", ""), key=f"edit_title_{iid}")

        device_options = get_all_device_names(all_customers)
        current_dev = issue.get("device", "N/A")
        dev_opts_full = sorted(set(device_options) | {current_dev})
        e_device = st.selectbox("Thiết bị", options=dev_opts_full,
                                 index=dev_opts_full.index(current_dev), key=f"edit_dev_{iid}")

        e_serial = st.text_input("Serial", value=issue.get("serial", ""), key=f"edit_serial_{iid}")
        e_status = st.selectbox("Trạng thái", options=["Pending", "Fixed"],
                                 index=0 if issue.get("status", "Pending") == "Pending" else 1, key=f"edit_status_{iid}")
        e_url = st.text_input("Link tài liệu", value=issue.get("url", ""), key=f"edit_url_{iid}")

        if st.button("Cập nhật thông tin Issue", key=f"update_issue_btn_{iid}"):
            update_issue(cid, iid, e_title.strip(), e_device, e_serial, e_status, e_url)
            st.rerun()


def render_add_activity_form(cid, iid):
    with st.expander("+ Thêm Activity"):
        a_name = st.text_input("Tên hoạt động", key=f"new_act_name_{iid}")
        a_date = st.date_input("Ngày thực hiện", value=date.today(), format="DD/MM/YYYY", key=f"new_act_date_{iid}")
        a_has_lead = st.checkbox("Có hạn phản hồi (Lead-time)?", key=f"new_act_haslt_{iid}")
        a_lead = st.date_input("Hạn phản hồi", value=date.today(), format="DD/MM/YYYY", key=f"new_act_lead_{iid}") \
            if a_has_lead else None
        a_pic = st.text_input("Người thực hiện (PIC)", key=f"new_act_pic_{iid}")
        a_close = st.checkbox("Đóng Issue này (Chuyển thành Fixed)", key=f"new_act_close_{iid}")
        a_result = st.text_area("Kết quả / Diễn giải chi tiết", key=f"new_act_result_{iid}")

        if st.button("💾 Lưu Hoạt Động", type="primary", key=f"save_act_btn_{iid}"):
            if a_name.strip():
                add_activity(cid, iid, a_name.strip(), a_date.strftime("%d/%m/%Y"), a_pic, a_result,
                             a_lead.strftime("%d/%m/%Y") if a_lead else "", a_close)
                st.rerun()
            else:
                st.warning("Vui lòng nhập tên hoạt động.")


def render_edit_activity_form(cid, iid, sid, step):
    with st.form(key=f"edit_form_{sid}"):
        e_name = st.text_input("Tên hoạt động", value=step.get("activity", ""))
        cur_date = parse_date_safe(step.get("date", "")) or date.today()
        e_date = st.date_input("Ngày thực hiện", value=cur_date, format="DD/MM/YYYY")
        cur_lead = parse_date_safe(step.get("lead_time", ""))
        e_has_lead = st.checkbox("Có hạn phản hồi?", value=bool(cur_lead))
        e_lead = st.date_input("Hạn phản hồi", value=cur_lead or date.today(), format="DD/MM/YYYY") \
            if e_has_lead else None
        e_pic = st.text_input("PIC", value=step.get("pic", ""))
        e_result = st.text_area("Kết quả", value=step.get("result", ""))
        e_close = st.checkbox("Đóng Issue (Fixed)")

        if st.form_submit_button("Cập nhật Hoạt Động"):
            update_activity(cid, iid, sid, e_name.strip(), e_date.strftime("%d/%m/%Y"), e_pic, e_result,
                            e_lead.strftime("%d/%m/%Y") if e_lead else "", e_close)
            st.session_state[f"editing_{sid}"] = False
            st.rerun()


def render_activities(cid, iid, issue, cust_name):
    st.markdown("##### 📝 Lịch Sử Xử Lý (Activities)")
    render_add_activity_form(cid, iid)

    steps = issue.get("steps") or {}
    if not steps:
        st.caption(f"Sự cố '{issue.get('title')}' chưa có nhật ký xử lý nào.")
        return

    today = date.today()
    for sid, step in sorted(steps.items(), key=lambda kv: kv[1].get("date", "")):
        lt = step.get("lead_time", "")
        deadline_display = "---"
        if lt:
            d = parse_date_safe(lt)
            if d:
                delta = (d - today).days
                if delta < 0:
                    deadline_display = f"⚠️ Trễ {abs(delta)} ngày"
                elif delta <= 3:
                    deadline_display = f"⏳ Còn {delta} ngày"
                else:
                    deadline_display = f"✅ {lt}"

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
            with c1:
                st.markdown(f"**{step.get('date')}** — {step.get('activity')}")
                st.caption(f"PIC: {step.get('pic', 'N/A')}  |  Hạn: {deadline_display}")
                if step.get("result"):
                    st.caption(step.get("result"))
            with c2:
                if st.button("✏️", key=f"edit_act_{sid}", help="Sửa"):
                    st.session_state[f"editing_{sid}"] = not st.session_state.get(f"editing_{sid}", False)
                    st.rerun()
            with c3:
                if st.button("🗑️", key=f"del_act_{sid}", help="Xóa"):
                    delete_activity(cid, iid, sid)
                    st.rerun()
            with c4:
                if st.button("⚡", key=f"export_task_{sid}", help="Tạo Task (TKB_TODO)"):
                    export_activity_to_task(cust_name, issue.get("title", iid), step)

            if st.session_state.get(f"editing_{sid}"):
                render_edit_activity_form(cid, iid, sid, step)


def render_issues_tab(all_customers):
    cid = st.session_state.get("selected_customer_id")
    if not cid or cid not in all_customers:
        st.info("👈 Chọn hoặc thêm 1 Khách hàng ở sidebar bên trái để bắt đầu.")
        return

    customer = all_customers[cid]
    st.subheader(f"📁 {customer.get('name', '')}")

    issues = customer.get("issues") or {}
    render_add_issue_form(cid, all_customers, len(issues) == 0)

    if not issues:
        st.caption("Khách hàng này chưa có Issue nào.")
        return

    render_issue_list(cid, issues)

    st.markdown("---")
    iid = st.session_state.selected_issue_id
    issue = issues[iid]
    render_edit_issue_form(cid, iid, issue, all_customers)
    render_activities(cid, iid, issue, customer.get("name", ""))


# ==========================================
# GIAO DIỆN: TAB "THỐNG KÊ"
# ==========================================
def render_stats_tab(all_customers):
    st.subheader("📊 Thống Kê Issues")
    mode = st.radio("Nhóm theo:", ["Khách hàng", "Tên Issue", "Thiết bị"], horizontal=True)

    stats = {}
    for cust in all_customers.values():
        for issue in (cust.get("issues") or {}).values():
            if mode == "Khách hàng":
                key = cust.get("name", "N/A")
            elif mode == "Tên Issue":
                key = issue.get("title", "N/A")
            else:
                key = issue.get("device", "N/A")

            e = stats.setdefault(key, {"Tổng số": 0, "Pending": 0, "Fixed": 0, "Quá hạn": 0})
            e["Tổng số"] += 1
            if issue.get("status", "Pending") == "Fixed":
                e["Fixed"] += 1
            else:
                e["Pending"] += 1
            if is_issue_overdue(issue):
                e["Quá hạn"] += 1

    if not stats:
        st.caption("Chưa có dữ liệu để thống kê.")
        return

    df = pd.DataFrame.from_dict(stats, orient="index").sort_values("Tổng số", ascending=False)
    st.caption(f"{len(df)} nhóm | {int(df['Tổng số'].sum())} issue")
    st.dataframe(df, use_container_width=True)
    st.markdown("###### Top 8 theo tổng số Issue")
    st.bar_chart(df["Tổng số"].head(8))


# ==========================================
# GIAO DIỆN: TAB "ĐỒNG BỘ DỮ LIỆU BAN ĐẦU" (chỉ dùng 1 lần)
# ==========================================
def render_import_tab(all_customers):
    st.subheader("⚙️ Đồng bộ dữ liệu ban đầu từ database.json")
    st.warning(
        "Chỉ nên dùng chức năng này **một lần duy nhất** để đưa dữ liệu cũ (từ bản Tkinter) lên Firebase. "
        "Chạy lại nhiều lần sẽ tạo trùng khách hàng, vì mỗi lần chạy sẽ sinh ID mới cho từng khách hàng."
    )

    if all_customers:
        st.info(f"Firebase hiện đã có **{len(all_customers)}** khách hàng. "
                "Nếu upload lại, dữ liệu mới sẽ được THÊM VÀO (không ghi đè) — có thể gây trùng lặp.")

    uploaded = st.file_uploader("Chọn file database.json (từ bản Tkinter)", type="json")
    if not uploaded:
        return

    try:
        raw = json.load(uploaded)
    except Exception as e:
        st.error(f"File JSON không hợp lệ: {e}")
        return

    st.write(f"Đọc được **{len(raw)}** khách hàng từ file.")
    confirm = st.checkbox("Tôi hiểu và muốn đồng bộ dữ liệu này lên Firebase")

    if confirm and st.button("🚀 Đồng bộ lên Firebase", type="primary"):
        count_cust, count_issue = 0, 0
        for cust_name, cust_data in raw.items():
            issues_out = {}
            for old_key, issue_data in (cust_data.get("issues") or {}).items():
                # Hỗ trợ cả 2 định dạng cũ: title-là-key (rất cũ) và id-là-key có field "title" (mới hơn)
                title = issue_data.get("title", old_key)
                new_iid = str(uuid.uuid4())[:8]
                steps_out = {}
                for step in issue_data.get("steps", []):
                    sid = step.get("id") or str(uuid.uuid4())[:8]
                    steps_out[sid] = {k: v for k, v in step.items() if k != "id"}
                issues_out[new_iid] = {
                    "title": title,
                    "device": issue_data.get("device", "N/A"),
                    "serial": issue_data.get("serial", "N/A"),
                    "status": issue_data.get("status", "Pending"),
                    "url": issue_data.get("url", ""),
                    "steps": steps_out,
                }
                count_issue += 1
            new_cid = str(uuid.uuid4())[:8]
            customers_ref().child(new_cid).set({"name": cust_name, "issues": issues_out})
            count_cust += 1

        st.success(f"Đã đồng bộ {count_cust} khách hàng, {count_issue} issue lên Firebase!")
        st.balloons()


# ==========================================
# MAIN
# ==========================================
def main():
    if not check_password():
        st.stop()

    init_firebase()

    st.title("🔧 Issue Follow — Hệ thống Quản lý & Gợi ý Khắc phục Sự cố Thiết bị")

    if "selected_customer_id" not in st.session_state:
        st.session_state.selected_customer_id = None
    if "selected_issue_id" not in st.session_state:
        st.session_state.selected_issue_id = None

    all_customers = load_all_customers()
    render_sidebar(all_customers)

    tab1, tab2, tab3 = st.tabs(["📋 Quản lý Issues", "📊 Thống Kê", "⚙️ Đồng bộ dữ liệu ban đầu"])
    with tab1:
        render_issues_tab(all_customers)
    with tab2:
        render_stats_tab(all_customers)
    with tab3:
        render_import_tab(all_customers)


if __name__ == "__main__":
    main()
