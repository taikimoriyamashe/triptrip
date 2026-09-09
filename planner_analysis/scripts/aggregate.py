#!/usr/bin/env python3
"""
①案件明細CSV (01_case_detail_2026.csv) から
②個人×月×役割区分の集計、③役割別月次集計、派生指標、分布、上位者、検算表 を作成し
CSV と Excel (planner_productivity_2026.xlsx) に出力する。

定義（README参照）:
- 役割区分 role_category: 実施日時点(as-of) の int_planner_metas から判定 (SE/EP/CP/その他)
- シフト単位 = shift_key (trial_lesson_id-planner_id-role_requirement_id-position_no)。1シフトに複数参加者行がある
- 参加者 = trial_lesson_reservation_id が紐づく行。同一プランナー×同一予約に役割違いの行が複数ある場合は
  participant_row_rank=1（プランナー>サポプラ>ファシ>リーダー）の1行だけを成果集計に使う（二重計上防止）
- リード成果 = counsel_role in (1on1リード, GCリード) の行の成約。GCアシストは別集計
- 社員（SE/generalist/CMM/メタなし社員）は業務委託レートを適用せず、シフト報酬・インセンティブとも0円（参考額は別列）
"""
import sys, os, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore', category=RuntimeWarning)

SRC = sys.argv[1] if len(sys.argv) > 1 else 'planner_analysis/output/01_case_detail_2026.csv'
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else 'planner_analysis/output'
ASOF_DATE = sys.argv[3] if len(sys.argv) > 3 else '2026-09-09'   # データ取得日（JST）
MIN_SAMPLE_PARTICIPANTS = 30   # ②個人×月の「サンプル不足」フラグ閾値（月間リード参加者数）
TOP_MIN_PARTICIPANTS = 10      # 上位者判定に入れる最低リード参加者数
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
num_cols = ['attendance_status','row_no_in_shift','participant_row_rank','is_conversion_gross','is_valid_conversion','is_coolingoff',
            'is_same_day_conversion_gross','is_later_conversion_gross','is_same_day_valid_conversion','is_later_valid_conversion',
            'incentive_yen_bq','incentive_yen_payable','shift_unit_yen','entranceamount_without_tax','membership_plan_amount',
            'conversion_sales_without_tax','virtual_cv','incentive_conversion_flg','position_no','days_enrolled_so_far']
for c in num_cols:
    df[c] = pd.to_numeric(df[c].replace('', np.nan), errors='coerce')
df['lesson_date'] = pd.to_datetime(df['lesson_date'])
df['is_future'] = df['lesson_date'] > pd.Timestamp(ASOF_DATE)
df['has_participant'] = df['trial_lesson_reservation_id'] != ''
df['is_primary_participant_row'] = df['has_participant'] & (df['participant_row_rank'] == 1)
df['is_dup_participant_row'] = df['has_participant'] & (df['participant_row_rank'] > 1)
df['is_lead_row'] = df['counsel_role'].isin(['1on1リード','GCリード'])
df['is_assist_row'] = df['counsel_role'].eq('GCアシスト')
df['is_1on1'] = df['counsel_role'].eq('1on1リード')
df['is_gc_lead'] = df['counsel_role'].eq('GCリード')
df['is_lounge'] = df['counsel_role'].eq('Lounge')
df['is_employee'] = df['is_employee_no_shift_fee'].str.lower().eq('true')
df['is_trial_membership_b'] = df['is_trial_membership'].str.lower().eq('true')
df['is_priority_b'] = df['is_shift_priority'].str.lower().eq('true')
# 社員はインセンティブ支払対象外（参考額は incentive_yen_payable に残す）
df['incentive_yen_payable'] = df['incentive_yen_payable'].fillna(0)
df['incentive_yen_paid'] = np.where(df['is_employee'], 0, df['incentive_yen_payable'])
# 収益は最終有効成約(ご入会)の行にのみ計上（クーオフは返金のため0）
for c in ['entranceamount_without_tax','membership_plan_amount','conversion_sales_without_tax']:
    df[c+'_valid'] = np.where(df['is_valid_conversion'].eq(1), df[c].fillna(0), 0)
df['first_month_revenue_valid'] = df['entranceamount_without_tax_valid'] + df['membership_plan_amount_valid']

# ---------- シフト単位テーブル（1行 = shift_key） ----------
first_rows = df[df['row_no_in_shift'] == 1].copy()
first_rows['shift_fee_yen'] = first_rows['shift_unit_yen'].fillna(0)
# そのシフトでリード参加者を担当したか（1シフト当たり成約の分母用）
lead_shift_keys = set(df.loc[df['is_primary_participant_row'] & df['is_lead_row'] & (df['attendance_status'] == 2), 'shift_key'])
first_rows['has_lead_participant'] = first_rows['shift_key'].isin(lead_shift_keys)
shift_tbl = first_rows[['shift_key','planner_id','planner_name','lesson_month','lesson_date','role_category','role_subcategory',
                        'role_name','role_status','attendance_status','attendance_status_text','lesson_type','is_priority_b','shift_fee_yen','is_future','is_employee','has_lead_participant']]

def shift_metrics(g):
    st = g['attendance_status']; att = st == 2
    return pd.Series({
        'shift_registered': len(g),
        'shift_registered_future': int(g['is_future'].sum()),
        'shift_registered_past': int((~g['is_future']).sum()),
        'shift_confirmed': int(st.isin([1,2,3,5]).sum()),       # 登録のうちご自愛(シフトカット)されていないもの
        'shift_attended': int(att.sum()),                         # 実施（全役割）
        'shift_attended_planner_role': int((att & (g['role_name'] == 'プランナー')).sum()),  # プランナー役割で実施
        'shift_attended_with_lead': int((att & g['has_lead_participant']).sum()),           # リード参加者を1人以上担当した実施シフト
        'shift_min_guarantee': int((st == 5).sum()),             # 最低保証（開催なし）
        'shift_jiai_cancel': int((st == 4).sum()),               # ご自愛 = シフトカット
        'shift_absent': int((st == 3).sum()),                    # 欠席
        'shift_unresolved_past': int(((st == 1) & (~g['is_future'])).sum()),  # 実施日超過だが出欠未反映
        'shift_priority_attended': int((att & g['is_priority_b']).sum()),
        'shift_role_planner': int((g['role_name'] == 'プランナー').sum()),
        'shift_role_leader': int((g['role_name'] == 'リーダー').sum()),
        'shift_role_facilitator': int((g['role_name'] == 'ファシリテーター').sum()),
        'shift_role_support': int((g['role_name'] == 'サポートプランナー').sum()),
        'shift_role_other': int((~g['role_name'].isin(['プランナー','リーダー','ファシリテーター','サポートプランナー'])).sum()),
        'shift_fee_yen': float(g['shift_fee_yen'].sum()),
        'shift_fee_planner_base_yen': float(g.loc[(g['role_name'] == 'プランナー') & att, 'shift_fee_yen'].sum()),
        'shift_fee_leader_facil_support_yen': float(g.loc[g['role_name'].isin(['リーダー','ファシリテーター','サポートプランナー']) & att, 'shift_fee_yen'].sum()),
        'shift_fee_min_guarantee_yen': float(g.loc[st == 5, 'shift_fee_yen'].sum()),
    })

def participant_metrics(g):
    p = g[g['is_primary_participant_row']]
    lead = p[p['is_lead_row']]
    asst = p[p['is_assist_row']]
    w_lead = np.where(lead['is_1on1'], 1.0, 0.7)
    out = {
        'participants_all': p['trial_lesson_reservation_id'].nunique(),
        'participants_lead': lead['trial_lesson_reservation_id'].nunique(),
        'dup_participant_rows_removed': int(g['is_dup_participant_row'].sum()),
        'lead_1on1': int(p['is_1on1'].sum()),
        'lead_gc': int(p['is_gc_lead'].sum()),
        'assist_gc': int(p['is_assist_row'].sum()),
        'lounge': int(p['is_lounge'].sum()),
        # リード成果
        'same_day_gross_lead': int(lead['is_same_day_conversion_gross'].sum()),
        'later_gross_lead': int(lead['is_later_conversion_gross'].sum()),
        'gross_lead': int(lead['is_conversion_gross'].sum()),
        'coolingoff_lead': int(lead['is_coolingoff'].sum()),
        'valid_lead': int(lead['is_valid_conversion'].sum()),
        'same_day_valid_lead': int(lead['is_same_day_valid_conversion'].sum()),
        'later_valid_lead': int(lead['is_later_valid_conversion'].sum()),
        'trial_membership_lead': int(lead['is_trial_membership_b'].sum()),
        # アシスト成果（参考: 誰の成果にするかは制度設計次第）
        'gross_assist': int(asst['is_conversion_gross'].sum()),
        'valid_assist': int(asst['is_valid_conversion'].sum()),
        'coolingoff_assist': int(asst['is_coolingoff'].sum()),
        # 全行ベース（GCアシスト・Lounge含む・参考値/既存集計に近い）
        'gross_all': int(p['is_conversion_gross'].sum()),
        'valid_all': int(p['is_valid_conversion'].sum()),
        'coolingoff_all': int(p['is_coolingoff'].sum()),
        # 加重（VCVモニタリング方式: 1on1=1.0, GCリード=0.7, GCアシスト=0.3）
        'weighted_valid': float((lead['is_valid_conversion'] * w_lead).sum() + (asst['is_valid_conversion'] * 0.3).sum()),
        'weighted_vcv': float((lead['virtual_cv'].fillna(0) * w_lead).sum() + (asst['virtual_cv'].fillna(0) * 0.3).sum()),
        'vcv_sum_lead': float(lead['virtual_cv'].fillna(0).sum()),
        # 報酬（成約インセンティブ: BQ算出。アシスト分含む。社員は paid=0）
        'incentive_yen_payable_ref': float(p['incentive_yen_payable'].sum()),
        'incentive_yen_paid': float(p['incentive_yen_paid'].sum()),
        'incentive_yen_paid_lead': float(lead['incentive_yen_paid'].sum()),
        'incentive_yen_paid_assist': float(asst['incentive_yen_paid'].sum()),
        # 収益（リード成約・最終有効のみ）
        'entrance_fee_valid_lead': float(lead['entranceamount_without_tax_valid'].sum()),
        'plan_amount_valid_lead': float(lead['membership_plan_amount_valid'].sum()),
        'first_month_revenue_valid_lead': float(lead['first_month_revenue_valid'].sum()),
        # 属性内訳（リード参加者）
        'lead_subsidy_target': int((lead['counseling_subsidy'] == '補助金対象').sum()),
        'lead_valid_subsidy_target': int(lead.loc[lead['counseling_subsidy'] == '補助金対象', 'is_valid_conversion'].sum()),
        'lead_difficulty_4_5_6': int(lead['difficulty_rank'].isin(['4','5','6']).sum()),
        'lead_valid_difficulty_4_5_6': int(lead.loc[lead['difficulty_rank'].isin(['4','5','6']), 'is_valid_conversion'].sum()),
        'lead_utm_other': int((lead['utm_pattern'] == 'その他').sum()),
        'lead_valid_utm_other': int(lead.loc[lead['utm_pattern'] == 'その他', 'is_valid_conversion'].sum()),
    }
    return pd.Series(out)

def rate(n, d):
    n = np.asarray(n, dtype=float); d = np.asarray(d, dtype=float)
    return np.where(d > 0, n / np.where(d > 0, d, 1), np.nan)

def add_rates(t):
    t['same_day_rate_lead'] = rate(t['same_day_gross_lead'], t['participants_lead'])
    t['gross_rate_lead'] = rate(t['gross_lead'], t['participants_lead'])
    t['coolingoff_rate_lead'] = rate(t['coolingoff_lead'], t['gross_lead'])
    t['valid_rate_lead'] = rate(t['valid_lead'], t['participants_lead'])
    t['valid_rate_all'] = rate(t['valid_all'], t['participants_all'])
    t['cancel_rate_shift'] = rate(t['shift_jiai_cancel'] + t['shift_absent'], t['shift_registered_past'])
    t['jiai_rate_shift'] = rate(t['shift_jiai_cancel'], t['shift_registered_past'])
    t['total_reward_yen'] = t['shift_fee_yen'] + t['incentive_yen_paid']
    t['valid_per_shift_all'] = rate(t['valid_lead'], t['shift_attended'])
    t['valid_per_lead_shift'] = rate(t['valid_lead'], t['shift_attended_with_lead'])
    t['valid_per_100_lead_shift'] = t['valid_per_lead_shift'] * 100
    t['participants_per_lead_shift'] = rate(t['participants_lead'], t['shift_attended_with_lead'])
    t['reward_per_valid_yen'] = rate(t['total_reward_yen'], t['valid_lead'])
    t['valid_per_reward_1man_yen'] = rate(t['valid_lead'] * 10000, t['total_reward_yen'])
    t['first_month_revenue_per_reward'] = rate(t['first_month_revenue_valid_lead'], t['total_reward_yen'])
    t['entrance_fee_per_reward'] = rate(t['entrance_fee_valid_lead'], t['total_reward_yen'])
    t['weighted_vcv_achievement'] = rate(t['weighted_valid'], t['weighted_vcv'])
    return t

def build(sub_df, keys):
    sub_shift = shift_tbl[shift_tbl['shift_key'].isin(sub_df['shift_key'])]
    a = sub_shift.groupby(keys, dropna=False).apply(shift_metrics, include_groups=False).reset_index()
    b = sub_df.groupby(keys, dropna=False).apply(participant_metrics, include_groups=False).reset_index()
    t = a.merge(b, on=keys, how='outer')
    num = [c for c in t.columns if c not in keys]
    t[num] = t[num].fillna(0)
    return add_rates(t)

# ---------- ②個人×月×役割 ----------
keys = ['planner_id','planner_name','lesson_month','role_category','role_subcategory']
ind = build(df, keys)
ind['is_employee'] = ind['role_category'].eq('SE') | ind['role_subcategory'].isin(['その他_generalist(社員)','その他_社員等(メタなし/未設定)','その他_CMM(拠点)','その他_other'])
multi = ind.groupby(['planner_id','lesson_month'])['role_category'].nunique().rename('n_roles_in_month').reset_index()
ind = ind.merge(multi, on=['planner_id','lesson_month'], how='left')
ind['role_changed_in_month'] = ind['n_roles_in_month'] > 1
ind['sample_insufficient_flag'] = ind['participants_lead'] < MIN_SAMPLE_PARTICIPANTS
ind['reward_note'] = np.where(ind['is_employee'], '社員（給与）: 業務委託レート非適用。total_reward_yen=0。incentive_yen_payable_ref は業務委託レート換算の参考額', '')
ind = ind.sort_values(['lesson_month','role_category','planner_id'])

# 役割区分粒度（サブ区分を畳む）: 上位者・分布用
def collapse_to_role(t, extra_keys):
    k = ['planner_id','planner_name'] + extra_keys + ['role_category']
    sum_cols = [c for c in t.columns if c not in k + ['role_subcategory','is_employee','n_roles_in_month','role_changed_in_month','sample_insufficient_flag','reward_note','period','months_active']
                and not (c.endswith('_rate_lead') or c.endswith('_rate_all') or c.endswith('_rate_shift') or c.startswith('valid_per') or c.startswith('participants_per') or c.endswith('_per_reward') or c in ['reward_per_valid_yen','weighted_vcv_achievement','total_reward_yen','valid_per_100_lead_shift'])]
    g = t.groupby(k, dropna=False)[sum_cols].sum().reset_index()
    return add_rates(g)

# ---------- 個人別 期間合計 ----------
def period_summary(mask, label):
    t = build(df[mask], ['planner_id','planner_name','role_category'])
    ma = df[mask & (df['attendance_status'] == 2)].groupby(['planner_id','planner_name','role_category'])['lesson_month'].nunique().rename('months_active').reset_index()
    t = t.merge(ma, on=['planner_id','planner_name','role_category'], how='left').fillna({'months_active': 0})
    t['is_employee'] = t['role_category'].eq('SE') | (t['role_category'].eq('その他') & (t['shift_fee_yen'] == 0) & (t['incentive_yen_paid'] == 0) & (t['incentive_yen_payable_ref'] > 0))
    t['sample_insufficient_flag'] = t['participants_lead'] < MIN_SAMPLE_PARTICIPANTS
    t['period'] = label
    return t
ind_period = pd.concat([period_summary(df['lesson_month'] >= '2026-07', '2026-07以降'),
                        period_summary(df['lesson_month'] >= '2026-01', '2026-01以降')], ignore_index=True)

# ---------- ③役割別月次 ----------
DIST_COLS = [('valid_rate_lead','valid_rate'), ('shift_attended','shifts'), ('valid_lead','valid_cnt'), ('valid_per_lead_shift','valid_per_lead_shift'),
             ('total_reward_yen','reward'), ('participants_lead','participants_lead'), ('coolingoff_rate_lead','coolingoff_rate')]
SUM_COLS = ['shift_registered','shift_registered_past','shift_registered_future','shift_confirmed','shift_attended','shift_attended_planner_role','shift_attended_with_lead',
            'shift_min_guarantee','shift_jiai_cancel','shift_absent','shift_unresolved_past','shift_priority_attended',
            'participants_all','participants_lead','dup_participant_rows_removed','lead_1on1','lead_gc','assist_gc','lounge',
            'same_day_gross_lead','later_gross_lead','gross_lead','coolingoff_lead','valid_lead','same_day_valid_lead','later_valid_lead','trial_membership_lead',
            'gross_assist','valid_assist','coolingoff_assist','gross_all','valid_all','coolingoff_all','weighted_valid','weighted_vcv','vcv_sum_lead',
            'shift_fee_yen','shift_fee_planner_base_yen','shift_fee_leader_facil_support_yen','shift_fee_min_guarantee_yen',
            'incentive_yen_payable_ref','incentive_yen_paid','incentive_yen_paid_lead','incentive_yen_paid_assist',
            'entrance_fee_valid_lead','plan_amount_valid_lead','first_month_revenue_valid_lead',
            'lead_subsidy_target','lead_valid_subsidy_target','lead_difficulty_4_5_6','lead_valid_difficulty_4_5_6','lead_utm_other','lead_valid_utm_other']

def role_agg(t_ind, group_cols):
    rows = []
    for key, g in t_ind.groupby(group_cols, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        r = dict(zip(group_cols, key))
        r['headcount_registered'] = g['planner_id'].nunique()
        r['headcount_active'] = g.loc[g['shift_attended'] > 0, 'planner_id'].nunique()
        r['headcount_with_lead'] = g.loc[g['participants_lead'] > 0, 'planner_id'].nunique()
        r['headcount_sample_ge30'] = g.loc[g['participants_lead'] >= MIN_SAMPLE_PARTICIPANTS, 'planner_id'].nunique()
        for c in SUM_COLS:
            r[c] = g[c].sum()
        # 個人分布（分母がある個人=リード参加者1人以上。全個人で算出し、サンプル不足は headcount_sample_ge30 で判断）
        q = g[g['participants_lead'] > 0]
        for col, name in DIST_COLS:
            s = pd.to_numeric(q[col], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()
            r[f'{name}_n'] = len(s)
            for stat, fn in [('min','min'), ('p25',0.25), ('median',0.5), ('p75',0.75), ('max','max')]:
                r[f'{name}_{stat}'] = (getattr(s, fn)() if isinstance(fn, str) else s.quantile(fn)) if len(s) else np.nan
        rows.append(r)
    t = pd.DataFrame(rows)
    t = add_rates(t)
    t['subsidy_valid_rate_lead'] = rate(t['lead_valid_subsidy_target'], t['lead_subsidy_target'])
    t['difficulty456_valid_rate_lead'] = rate(t['lead_valid_difficulty_4_5_6'], t['lead_difficulty_4_5_6'])
    t['utm_other_valid_rate_lead'] = rate(t['lead_valid_utm_other'], t['lead_utm_other'])
    t['shifts_per_active_planner'] = rate(t['shift_attended'], t['headcount_active'])
    t['valid_per_active_planner'] = rate(t['valid_lead'], t['headcount_active'])
    t['reward_per_active_planner'] = rate(t['total_reward_yen'], t['headcount_active'])
    return t

ind_role_month = collapse_to_role(ind, ['lesson_month'])
role_m = role_agg(ind_role_month, ['lesson_month','role_category']).sort_values(['lesson_month','role_category'])
role_sub_m = role_agg(ind, ['lesson_month','role_category','role_subcategory']).sort_values(['lesson_month','role_category','role_subcategory'])
role_period = pd.concat([role_agg(ind_period[ind_period['period'] == p], ['role_category']).assign(period=p) for p in ['2026-07以降','2026-01以降']], ignore_index=True)

# ---------- 上位者（成約率とシフト数の両方が役割中央値以上） ----------
def top_performers(t, label):
    out = []
    for role, g in t.groupby('role_category'):
        q = g[g['participants_lead'] >= TOP_MIN_PARTICIPANTS].copy()
        if len(q) == 0: continue
        med_rate = q['valid_rate_lead'].median(); med_shift = q['shift_attended_with_lead'].median()
        q['role_median_valid_rate'] = med_rate; q['role_median_lead_shifts'] = med_shift; q['n_in_role'] = len(q)
        q['is_top_both'] = (q['valid_rate_lead'] >= med_rate) & (q['shift_attended_with_lead'] >= med_shift)
        q['rank_valid_rate_in_role'] = q['valid_rate_lead'].rank(ascending=False, method='min')
        q['rank_lead_shift_in_role'] = q['shift_attended_with_lead'].rank(ascending=False, method='min')
        q['rank_valid_cnt_in_role'] = q['valid_lead'].rank(ascending=False, method='min')
        q['sample_insufficient_flag'] = q['participants_lead'] < MIN_SAMPLE_PARTICIPANTS
        q['period'] = label
        out.append(q)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
top_period = top_performers(ind_period[ind_period['period'] == '2026-07以降'], '2026-07以降')
top_month = pd.concat([top_performers(ind_role_month[ind_role_month['lesson_month'] == m], m) for m in sorted(ind_role_month['lesson_month'].unique())], ignore_index=True)

# ---------- 検算表 ----------
def recon_block(mask, basis):
    sub = df[mask & df['is_primary_participant_row']]
    t = sub.groupby('lesson_month').agg(participants=('trial_lesson_reservation_id','nunique'), gross=('is_conversion_gross','sum'),
                                       coolingoff=('is_coolingoff','sum'), valid=('is_valid_conversion','sum')).reset_index()
    t['valid_rate'] = rate(t['valid'], t['participants']); t['basis'] = basis
    return t
ref = pd.DataFrame({'lesson_month':['2026-07','2026-08','2026-09'],'participants':[793,669,191],'valid':[310,264,59],'valid_rate':[0.3909,0.3946,0.3089],'basis':'依頼記載の既存集計「その他」参考値'})
recon = pd.concat([ref,
    recon_block((df['current_planner_term'] == 'SE') & (df['role_status'] == 'プランナー'), 'A: 現在値planner_term=SE & role_status=プランナー（既存集計の再現、主行のみ）'),
    recon_block(df['role_category'] == 'SE', 'B: 本抽出 実施日時点SE（全シフト役割・GCアシスト含む）'),
    recon_block((df['role_category'] == 'SE') & df['is_lead_row'], 'C: 本抽出 実施日時点SE & リード（1on1/GCリード）のみ = 本表の成約率定義'),
    ], ignore_index=True)

# ---------- 対象者リスト照合 ----------
se_list = ['三島加菜','野間香南子','平林花織','平川美希','安達美里','吉野由佳','平原奈津子','大場史子','井野麗菜','平川晴美','中村江里','木村駿','松本紗彩','佐藤智子','五師光葉']
pl = df.groupby(['planner_id','planner_name']).agg(first_lesson=('lesson_date','min'), last_lesson=('lesson_date','max'),
        roles_asof=('role_subcategory', lambda s: '/'.join(sorted(set(s)))), current_term=('current_planner_term','first'), current_grade=('current_planner_grade','first'),
        planner_status_asof_last=('asof_planner_status','last'),
        shifts_attended=('attendance_status', lambda s: int((s==2).sum())), rows=('shift_key','size')).reset_index()
pl['name_norm'] = pl['planner_name'].str.replace(r'\s+','',regex=True)
pl['in_se_list'] = pl['name_norm'].isin(se_list)
pl['ever_SE_asof'] = pl['roles_asof'].str.contains('SE')
pl['flag'] = np.select([pl['in_se_list'] & ~pl['ever_SE_asof'], ~pl['in_se_list'] & pl['ever_SE_asof'], pl['planner_name'].str.contains(r'\s', regex=True)],
                       ['リストにあるがSE判定なし','SE判定だがリストにない','氏名に空白（表記揺れ候補）'], '-')
missing = [n for n in se_list if n not in set(pl['name_norm'])]
dup_names = pl[pl.duplicated('name_norm', keep=False)]
roster = pl.sort_values(['in_se_list','planner_name'], ascending=[False, True])

# ---------- 役割変更履歴 ----------
chg = df.groupby(['planner_id','planner_name','role_category','role_subcategory','asof_generation','asof_career_path','asof_effective_at','role_source']).agg(
        first_lesson=('lesson_date','min'), last_lesson=('lesson_date','max'), rows=('shift_key','size')).reset_index()
chg_multi = chg[chg.groupby('planner_id')['role_category'].transform('nunique') > 1].sort_values(['planner_id','first_lesson'])

# ---------- 出力 ----------
ind.to_csv(f'{OUT_DIR}/02_individual_month.csv', index=False)
ind_period.to_csv(f'{OUT_DIR}/02b_individual_period.csv', index=False)
role_m.to_csv(f'{OUT_DIR}/03_role_month.csv', index=False)
role_sub_m.to_csv(f'{OUT_DIR}/03b_role_subcategory_month.csv', index=False)
role_period.to_csv(f'{OUT_DIR}/03c_role_period.csv', index=False)
top_period.to_csv(f'{OUT_DIR}/04_top_performers_period.csv', index=False)
top_month.to_csv(f'{OUT_DIR}/04b_top_performers_month.csv', index=False)
recon.to_csv(f'{OUT_DIR}/05_reconciliation.csv', index=False)
roster.to_csv(f'{OUT_DIR}/06_planner_roster_check.csv', index=False)
chg_multi.to_csv(f'{OUT_DIR}/07_role_change_history.csv', index=False)

with pd.ExcelWriter(f'{OUT_DIR}/planner_productivity_2026.xlsx', engine='openpyxl') as xw:
    pd.DataFrame({'項目':['データ取得日(JST)','対象期間(登録シフトの実施日)','明細行数','うち参加者重複行(成果集計から除外)','個人×月サンプル不足閾値(リード参加者)','上位者判定の最低リード参加者','SEリスト未検出氏名','定義書'],
                  '値':[ASOF_DATE, f"{df['lesson_date'].min().date()}〜{df['lesson_date'].max().date()}", len(df), int(df['is_dup_participant_row'].sum()), MIN_SAMPLE_PARTICIPANTS, TOP_MIN_PARTICIPANTS, ', '.join(missing) or 'なし', 'planner_analysis/README.md']}).to_excel(xw, sheet_name='README', index=False)
    role_m.to_excel(xw, sheet_name='③役割別月次', index=False)
    role_sub_m.to_excel(xw, sheet_name='③b役割サブ区分別月次', index=False)
    role_period.to_excel(xw, sheet_name='③c役割別期間合計', index=False)
    ind.to_excel(xw, sheet_name='②個人×月', index=False)
    ind_period.to_excel(xw, sheet_name='②b個人×期間', index=False)
    top_period.to_excel(xw, sheet_name='④上位者(7月以降)', index=False)
    top_month.to_excel(xw, sheet_name='④b上位者(月次)', index=False)
    recon.to_excel(xw, sheet_name='⑤検算', index=False)
    roster.to_excel(xw, sheet_name='⑥対象者照合', index=False)
    dup_names.to_excel(xw, sheet_name='⑥b同名複数ID', index=False)
    chg_multi.to_excel(xw, sheet_name='⑦役割変更履歴', index=False)
    drop_cols = ['is_future','has_participant','is_lead_row','is_assist_row','is_1on1','is_gc_lead','is_lounge','is_employee','is_trial_membership_b','is_priority_b']
    df[df['lesson_month'] >= '2026-07'].drop(columns=drop_cols).to_excel(xw, sheet_name='①明細(2026-07以降)', index=False)

print('rows', len(df), '| dup participant rows', int(df['is_dup_participant_row'].sum()), '| individual-month', len(ind), '| role-month', len(role_m), '| missing SE names:', missing)
print(role_m[['lesson_month','role_category','headcount_active','shift_attended','shift_attended_with_lead','participants_lead','valid_lead','valid_rate_lead','coolingoff_rate_lead','total_reward_yen','reward_per_valid_yen','valid_rate_median','valid_rate_n']].to_string())
print(recon.to_string())
