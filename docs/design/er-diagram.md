# ER 图（初稿，M0）

> 数据模型三层的核心思想：**逻辑文件（user_file）→ 版本（file_version）→ 物理对象（storage_object）**。
> 版本是引用计数的事实来源；物理对象按服务端核验的 (sha256, size) 全局去重。
> 本图为设计初稿，随 M1–M8 里程碑逐表落地并修订。

```mermaid
erDiagram
    user ||--o{ space : "注册建个人空间"
    user ||--o{ space_member : ""
    space ||--o{ space_member : ""
    space ||--o{ user_file : ""
    space ||--|| quota_usage : ""
    user_file ||--o{ file_version : "current_version_id 指针"
    file_version }o--|| storage_object : "引用计数来源"
    user ||--o{ upload_task : ""
    space ||--o{ upload_task : ""
    upload_task ||--o{ upload_part : ""
    user ||--o{ share : ""
    user_file ||--o{ share : ""
    share ||--o{ share_session : ""
    user ||--o{ login_log : ""
    user ||--o{ refresh_session : "可撤销刷新会话族（M1）"
    user ||--o{ operation_log : ""
    space ||--o{ file_index : ""

    user {
        bigint id PK
        varchar username UK
        varchar password_hash "bcrypt"
        bigint quota_bytes "个人空间额度"
        datetime created_at
    }
    space {
        bigint id PK
        enum type "PERSONAL|TEAM"
        varchar name
        bigint owner_id FK
    }
    space_member {
        bigint id PK
        bigint space_id FK
        bigint user_id FK
        enum role "OWNER|ADMIN|EDITOR|VIEWER"
    }
    user_file {
        bigint id PK
        bigint space_id FK
        bigint parent_id "NULL=根"
        varchar name "同目录活跃唯一"
        tinyint is_folder
        bigint current_version_id "文件才有"
        int generation "改名/移动/删除/切版本 +1"
        enum status "NORMAL|TRASH|DELETED"
        datetime deleted_at "回收站计时"
        bigint size_snapshot
        datetime created_at
        datetime updated_at
    }
    file_version {
        bigint id PK
        bigint file_id FK
        int version_no "file 内唯一"
        bigint storage_object_id FK
        bigint size "逻辑大小（计配额）"
        datetime created_at
    }
    storage_object {
        bigint id PK
        char sha256 "UK(sha256,size)"
        bigint size
        varchar bucket "formal|candidate"
        varchar object_key "UUID，不含用户输入"
        int ref_count "对账汇总值"
        enum status "ACTIVE|DELETING|DELETED"
        datetime created_at
    }
    upload_task {
        bigint id PK
        uuid upload_id UK
        bigint user_id FK
        bigint space_id FK
        bigint parent_id
        bigint target_file_id "同名=新版本意图"
        int target_generation "冲突校验快照"
        varchar file_name
        char declared_sha256
        bigint size
        bigint reserved_bytes "配额预留"
        int chunk_size
        int chunk_count
        enum status "CREATED|UPLOADING|VERIFYING|COMPLETED|CANCELLED|EXPIRED|FAILED"
        datetime expire_at
        bigint result_version_id "完成幂等键"
    }
    upload_part {
        bigint id PK
        bigint task_id FK
        int part_no "UK(task_id,part_no)"
        char checksum
        bigint size
        varchar etag
    }
    share {
        bigint id PK
        uuid share_token UK "uuid4 防枚举"
        bigint file_id FK
        bigint owner_id FK
        varchar extract_code_hash "bcrypt"
        datetime expire_at
        int max_visits
        int visit_count "原子条件扣减"
        enum status "ACTIVE|CANCELLED"
        int auth_epoch "撤销+1，会话失效开关"
    }
    share_session {
        bigint id PK
        bigint share_id FK
        bigint version_id "绑定版本"
        int auth_epoch
        varchar idempotency_key UK
        datetime expire_at
    }
    quota_usage {
        bigint space_id PK
        bigint used_bytes "含回收站与历史版本"
        bigint reserved_bytes "进行中上传预留"
    }
    outbox {
        bigint id PK
        varchar event_type
        json payload
        enum status
        int retry_count
    }
    file_index {
        bigint id PK
        bigint file_id FK
        bigint space_id FK "权限过滤依据"
        text content_text "FULLTEXT ngram"
    }
    login_log {
        bigint id PK
        varchar username
        varchar ip
        enum result "SUCCESS|FAILED|LOCKED"
        datetime created_at
    }
    refresh_session {
        bigint id PK
        varchar family_id "登录=新族，刷新=族内轮换"
        bigint user_id FK
        varchar token_hash UK "SHA-256，重用检测依据"
        datetime expires_at
        datetime revoked_at "NULL=活跃"
        varchar revoked_reason "rotated|logout|password_changed|reuse_detected"
    }
    operation_log {
        bigint id PK
        bigint user_id FK
        varchar action
        varchar target
        json detail
        varchar ip
        datetime created_at
    }
```

## 关键约束（落地时逐一实现）

1. `space_member` 唯一 `(space_id, user_id)`；注册事务内建个人空间 + OWNER 成员。
2. `user_file` 同目录活跃唯一：唯一索引含"活跃标志"列，非 NORMAL 置 NULL（MySQL 唯一索引不约束 NULL）。
3. `storage_object` 唯一 `(sha256, size)`；`ref_count` 与版本引用表同事务增减，对账任务校验一致。
4. `file_version` 唯一 `(file_id, version_no)`；配额 = 保留版本逻辑大小之和（同内容多版本分别计）。
5. `upload_task` 的 `upload_id` 唯一；COMPLETED 幂等返回同一 `result_version_id`。
6. `share_session` 幂等键唯一；`auth_epoch` 不匹配即拒绝（撤销语义）。
