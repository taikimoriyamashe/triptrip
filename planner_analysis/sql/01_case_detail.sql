-- ============================================================
-- ①案件単位の明細（1行 = プランナー×体験レッスン×参加者予約。参加者が紐づかないシフトは1行）
-- 参照: shelikes-001.sheinc_marts_fy24.likes_trial_lesson_planner_results（主）
--       shelikes-001.sheinc_intermediate.int_planner_metas（役割履歴 as-of）
--       shelikes-001.sheinc_marts_fy24.likes_trial_lesson_enhanced（参加者属性・収益・クーオフ）
--       shelikes-001.sheinc_marts_fy24.likes_conversions（売上）
--       shelikes-001.sheinc_marts_fy24.likes_member_terms（受講開始・退会 = LTV代理）
-- パラメータ: @start_date / @end_date は実行時に文字列置換
-- ============================================================
WITH metas AS (
  SELECT
    planner_id,
    effective_at,
    created_at,
    LEAD(effective_at) OVER (PARTITION BY planner_id ORDER BY effective_at, created_at) AS next_effective_at,
    ROW_NUMBER() OVER (PARTITION BY planner_id ORDER BY effective_at, created_at) AS rn_first,
    career_path,
    generation,
    planner_status
  FROM `shelikes-001.sheinc_intermediate.int_planner_metas`
),
first_meta AS (
  SELECT planner_id, career_path, generation, planner_status, effective_at
  FROM metas WHERE rn_first = 1
),
r AS (
  SELECT *
  FROM `shelikes-001.sheinc_marts_fy24.likes_trial_lesson_planner_results`
  WHERE DATE(starts_at) BETWEEN '@start_date' AND '@end_date'
),
asof AS (
  SELECT
    r.*,
    m.career_path AS asof_career_path,
    m.generation  AS asof_generation,
    m.planner_status AS asof_planner_status,
    m.effective_at AS asof_effective_at,
    fm.career_path AS first_career_path,
    fm.generation  AS first_generation,
    fm.effective_at AS first_effective_at
  FROM r
  LEFT JOIN metas m
    ON r.planner_id = m.planner_id
   AND r.starts_at >= m.effective_at
   AND (m.next_effective_at IS NULL OR r.starts_at < m.next_effective_at)
  LEFT JOIN first_meta fm ON r.planner_id = fm.planner_id
),
classified AS (
  SELECT
    a.*,
    -- 役割判定に使う値（as-of → 初回メタで後方補完 → 現在値）
    CASE WHEN a.asof_effective_at IS NOT NULL THEN 'asof'
         WHEN a.first_effective_at IS NOT NULL THEN 'backfilled_first_meta'
         WHEN a.planner_term IS NOT NULL OR a.planner_grade IS NOT NULL THEN 'current_from_results'
         ELSE 'no_meta' END AS role_source,
    CASE WHEN a.asof_effective_at IS NOT NULL THEN a.asof_generation  WHEN a.first_effective_at IS NOT NULL THEN a.first_generation  ELSE a.planner_term  END AS eff_generation,
    CASE WHEN a.asof_effective_at IS NOT NULL THEN a.asof_career_path WHEN a.first_effective_at IS NOT NULL THEN a.first_career_path ELSE a.planner_grade END AS eff_career_path
  FROM asof a
),
detail AS (
  SELECT
    c.*,
    CASE
      WHEN c.eff_generation IN ('SE','SE_onboarding') THEN 'SE'
      WHEN c.eff_career_path = 'expert' THEN 'EP'
      WHEN c.eff_career_path = 'career' THEN 'CP'
      ELSE 'その他'
    END AS role_category,
    CASE
      WHEN c.eff_generation IN ('SE','SE_onboarding') THEN CONCAT('SE(', IFNULL(c.eff_generation,''), ')')
      WHEN c.eff_career_path = 'expert' THEN 'EP'
      WHEN c.eff_career_path = 'career' THEN 'CP'
      WHEN c.eff_career_path = 'generalist' THEN 'その他_generalist(社員)'
      WHEN c.eff_generation = 'CMM' THEN 'その他_CMM(拠点)'
      WHEN c.eff_career_path IN ('lead','specialist') THEN CONCAT('その他_legacy_', c.eff_career_path)
      WHEN c.eff_career_path = 'other' THEN 'その他_other'
      WHEN c.eff_generation = '社員含むその他' OR c.eff_career_path IS NULL OR c.eff_career_path = '' THEN 'その他_社員等(メタなし/未設定)'
      ELSE 'その他_unknown'
    END AS role_subcategory
  FROM classified c
)
SELECT
  -- 識別子
  d.trial_lesson_id,
  d.trial_lesson_reservation_id,
  d.participants_user_id,
  d.planner_id,
  d.planner_name,
  -- シフトキー: 体験レッスン×プランナー×ロール要件×ポジション（同一ポジションに役割違いが存在するため role_requirement_id を含める）
  CONCAT(CAST(d.trial_lesson_id AS STRING), '-', CAST(d.planner_id AS STRING), '-', IFNULL(CAST(d.trial_lesson_role_requirement_id AS STRING),'x'), '-', IFNULL(CAST(d.position_no AS STRING),'x')) AS shift_key,
  d.trial_lesson_role_requirement_id, d.role_id,
  ROW_NUMBER() OVER (PARTITION BY d.trial_lesson_id, d.planner_id, d.trial_lesson_role_requirement_id, d.position_no ORDER BY d.trial_lesson_reservation_id) AS row_no_in_shift,
  -- 参加者の主担当行: 同一プランナー×同一予約に役割違いの行が複数ある場合、プランナー>サポプラ>ファシ>リーダー>その他 の優先で1行を主行にする（成果の二重計上防止）
  CASE WHEN d.trial_lesson_reservation_id IS NULL THEN 1 ELSE
    ROW_NUMBER() OVER (PARTITION BY d.planner_id, d.trial_lesson_reservation_id
      ORDER BY CASE d.role_name WHEN 'プランナー' THEN 1 WHEN 'サポートプランナー' THEN 2 WHEN 'ファシリテーター' THEN 3 WHEN 'リーダー' THEN 4 ELSE 5 END, d.position_no) END AS participant_row_rank,
  -- 日時
  d.starts_at, d.ends_at, DATE(d.starts_at) AS lesson_date, FORMAT_DATE('%Y-%m', DATE(d.starts_at)) AS lesson_month,
  d.lesson_start_time, d.is_shift_priority, d.is_shift_priority_detail,
  -- レッスン属性
  d.lesson_type, d.title, d.lesson_status_text, d.lounge, d.service_key,
  -- 役割（実施日時点）
  d.role_category, d.role_subcategory, d.role_source, d.eff_generation AS asof_generation, d.eff_career_path AS asof_career_path,
  d.asof_planner_status, d.asof_effective_at,
  d.planner_term AS current_planner_term, d.planner_grade AS current_planner_grade,
  -- シフト役割・状態
  d.role_status, d.role_name, d.position_no, d.assign_status_text, d.is_countable_assign,
  d.attendance_status, d.attendance_status_text, d.assign_registered_at, d.assign_cancelled_at,
  -- カウンセリング形式
  d.counselings_style, d.gc_style,
  CASE WHEN d.trial_lesson_reservation_id IS NULL THEN NULL
       WHEN d.counselings_style IN ('1on1','1on2','1on3') THEN '1on1リード'
       WHEN d.counselings_style = 'GC' AND d.gc_style = 'lead' THEN 'GCリード'
       WHEN d.counselings_style = 'GC' AND d.gc_style = 'assistant' THEN 'GCアシスト'
       WHEN d.counselings_style = 'Lounge' THEN 'Lounge'
       WHEN d.counselings_style = '離脱' THEN '離脱'
       ELSE IFNULL(d.counselings_style, '形式不明') END AS counsel_role,
  e.lead_planner_user_id, e.lead_planner_user_name, e.assistant_planner_user_id, e.assistant_planner_user_name,
  CASE WHEN e.lead_planner_user_id = d.planner_id THEN 'lead'
       WHEN e.assistant_planner_user_id = d.planner_id THEN 'assist'
       WHEN d.trial_lesson_reservation_id IS NULL THEN NULL
       ELSE 'other' END AS planner_relation_in_enhanced,
  d.pair_planner_id, d.pair_planner_name, d.pair_planner_grade,
  -- 成果
  d.conversion_status,
  CASE WHEN d.conversion_status IN ('ご入会','クーリングオフ') THEN 1 ELSE 0 END AS is_conversion_gross,
  CASE WHEN d.conversion_status = 'ご入会' THEN 1 ELSE 0 END AS is_valid_conversion,
  CASE WHEN d.conversion_status = 'クーリングオフ' THEN 1 ELSE 0 END AS is_coolingoff,
  d.is_the_day_cv,
  CASE WHEN d.conversion_status IN ('ご入会','クーリングオフ') AND d.is_the_day_cv THEN 1 ELSE 0 END AS is_same_day_conversion_gross,
  CASE WHEN d.conversion_status IN ('ご入会','クーリングオフ') AND NOT IFNULL(d.is_the_day_cv, FALSE) THEN 1 ELSE 0 END AS is_later_conversion_gross,
  CASE WHEN d.conversion_status = 'ご入会' AND d.is_the_day_cv THEN 1 ELSE 0 END AS is_same_day_valid_conversion,
  CASE WHEN d.conversion_status = 'ご入会' AND NOT IFNULL(d.is_the_day_cv, FALSE) THEN 1 ELSE 0 END AS is_later_valid_conversion,
  d.purchased_at, d.refunded_at, DATE(d.refunded_at) AS coolingoff_date,
  d.is_trial_membership, d.trial_membership_starts_at,
  d.incentive_conversion_flg, d.incentive_coolingoff_flg,
  -- 成果に影響する属性
  d.incentive_status, d.incentive_counselings_style,
  REGEXP_EXTRACT(d.incentive_status, r'難易度(\d)') AS difficulty_rank,
  e.virtual_cv_rank, e.virtual_cv,
  d.attended_intention_level, e.applied_intention_level, e.last_intention_level,
  d.counseling_subsidy, e.is_from_subsidy_ads,
  e.utm_pattern, e.last_utm_source, e.last_utm_medium, e.last_utm_campaign, e.applied_source, e.traffic_source_type,
  e.entrance_plan_name, e.entrance_item_name, e.payment_method, e.installment_type, e.installment_count,
  e.age_at_trial_lessons_group_5_years, e.prefecture, e.question_she_need_2,
  d.follow_up_status_today_text, d.non_enrollment_reason,
  d.suggested_campaign_text, d.suggested_loan_text, d.suggested_trial_membership_text,
  -- 担当チーム（プランナーの担当GL/CV担当）
  d.charge_gl_user_id, d.charge_gl_user_name, d.charge_cv_user_id, d.charge_cv_user_name,
  -- 報酬（業務委託レートカード 2026-07-16版 業務規定 / 成約インセンティブはBQ算出値）
  d.conversion_incentive AS incentive_yen_bq,  -- BQ算出の成約インセンティブ（ご入会かつ支払対象のみ値が入る）
  CASE WHEN d.conversion_status = 'ご入会' AND d.incentive_conversion_flg = 1 THEN d.conversion_incentive ELSE 0 END AS incentive_yen_payable,
  CASE WHEN d.role_category = 'SE' OR d.role_subcategory IN ('その他_generalist(社員)','その他_社員等(メタなし/未設定)','その他_CMM(拠点)','その他_other') THEN TRUE ELSE FALSE END AS is_employee_no_shift_fee,
  CASE
    WHEN d.role_category = 'SE' OR d.role_subcategory IN ('その他_generalist(社員)','その他_社員等(メタなし/未設定)','その他_CMM(拠点)','その他_other') THEN 0  -- 社員（SE/generalist/CMM/メタなし社員/旧other=2026-06-03以前のgeneralist・CMM相当）: 給与制、シフト単価なし
    WHEN IFNULL(d.attendance_status, 0) = 5 THEN 1500  -- 最低保証（開催なし）
    WHEN IFNULL(d.attendance_status, 0) <> 2 THEN 0    -- 未実施（登録中/欠席/ご自愛/フィジビリ）
    WHEN d.role_name = 'リーダー' THEN 10000
    WHEN d.role_name = 'ファシリテーター' THEN 1500
    WHEN d.role_name = 'サポートプランナー' THEN 1500
    WHEN d.role_name = 'プランナー' AND d.role_category = 'EP' THEN 4000
    WHEN d.role_name = 'プランナー' AND d.role_category = 'CP' THEN 2500
    WHEN d.role_name = 'プランナー' THEN 2500 -- その他の業務委託（legacy/other）は暫定CP単価
    ELSE 0 END AS shift_unit_yen,
  -- 収益
  e.entranceamount_without_tax, e.membership_plan_amount, e.membership_plan_discount_amount, e.entrance_plan_discount_amount,
  cv.sales_without_tax AS conversion_sales_without_tax, cv.is_subsidy, cv.subsidy_type, cv.item_name AS conversion_item_name,
  mt.likes_begin_date, mt.likes_withdrawal_date, mt.present_plan_name,
  DATE_DIFF(COALESCE(mt.likes_withdrawal_date, CURRENT_DATE('Asia/Tokyo')), mt.likes_begin_date, DAY) AS days_enrolled_so_far,
  e.is_valid_conversions AS enhanced_is_valid_conversion, e.is_coolingoff AS enhanced_is_coolingoff, e.is_attended AS enhanced_is_attended,
  e.conversion_status AS enhanced_conversion_status,
  d.counseling_review_url, d.updated_at AS source_updated_at
FROM detail d
LEFT JOIN `shelikes-001.sheinc_marts_fy24.likes_trial_lesson_enhanced` e
  ON d.trial_lesson_reservation_id = e.trial_lesson_reservation_id
LEFT JOIN `shelikes-001.sheinc_marts_fy24.likes_conversions` cv
  ON e.order_id = cv.order_id
LEFT JOIN `shelikes-001.sheinc_marts_fy24.likes_member_terms` mt
  ON e.membership_term_id = mt.membership_term_id
ORDER BY d.starts_at, d.trial_lesson_id, d.planner_id, d.trial_lesson_reservation_id
