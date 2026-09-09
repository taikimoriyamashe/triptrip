#!/usr/bin/env python3
"""
①案件明細CSV (01_case_detail_2026.csv) から
②個人×月×役割区分の集計、③役割別月次集計、派生指標、分布、上位者、検算表 を作成し
CSV と Excel (planner_productivity_2026.xlsx) に出力する。

定義（README参照）:
- 役割区分 role_category: 実施日時点(as-of) の int_planner_metas から判定 (SE/EP/CP/その他)
- シフト単位 = shift_key (trial_lesson_id-planner_id-position_no)。1シフトに複数参加者行がある
- 参加者 = trial_lesson_reservation_id が紐づく行（planner attendance=2 のみ実質存在）
- リード成果 = counsel_role in (1on1リード, GCリード) の行の成約。GCアシストは別集計
"""
import sys, os
import numpy as np
import pandas as pd

SRC = sys.argv[1] if len(sys.argv) > 1 else 'planner_analysis/output/01_case_detail_2026.csv'
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else 'planner_analysis/output'
ASOF_DATE = sys.argv[3] if len(sys.argv) > 3 else '2026-09-09'   # データ取得日（JST）
MIN_SAMPLE_PARTICIPANTS = 30   # サンプル不足フラグの閾値（月間リード参加者数）
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
num_cols = ['attendance_status','row_no_in_shift','is_conversion_gross','is_valid_conversion','is_coolingoff',
            'is_same_day_conversion_gross','is_later_conversion_gross','is_same_day_valid_conversion','is_later_valid_conversion',
            'incentive_yen_gross','incentive_yen_payable','shift_unit_yen','entranceamount_without_tax','membership_plan_amount',
            'conversion_sales_without_tax','virtual_cv','incentive_conversion_flg','position_no','days_enrolled_so_far']
for c in num_cols:
    df[c] = pd.to_numeric(df[c].replace('', np.nan), errors='coerce')
df['lesson_date'] = pd.to_datetime(df['lesson_date'])
df['is_future'] = df['lesson_date'] > pd.Timestamp(ASOF_DATE)
df['has_participant'] = df['trial_lesson_reservation_id'] != ''
df['is_lead_row'] = df['counsel_role'].isin(['1on1リード','GCリード'])
df['is_assist_row'] = df['counsel_role'].eq('GCアシスト')
df['is_1on1'] = df['counsel_role'].eq('1on1リード')
df['is_gc_lead'] = df['counsel_role'].eq('GCリード')
df['is_lounge'] = df['counsel_role'].eq('Lounge')
df['is_employee'] = df['is_employee_no_shift_fee'].str.lower().eq('true')
df['is_trial_membership_b'] = df['is_trial_membership'].str.lower().eq('true')
# 収益は最終有効成約(ご入会)の行にのみ計上（クーオフは返金のため0）
for c in ['entranceamount_without_tax','membership_plan_amount','conversion_sales_without_tax']:
    df[c+'_valid'] = np.where(df['is_valid_conversion'].eq(1), df[c].fillna(0), 0)
df['first_month_revenue_valid'] = df['entranceamount_without_tax_valid'] + df['membership_plan_amount_valid']

# ---------- シフト単位テーブル ----------
first_rows = df[df['row_no_in_shift'] == 1].copy()
first_rows['shift_fee_yen'] = first_rows['shift_unit_yen'].fillna(0)
shift_tbl = first_rows[['shift_key','planner_id','planner_name','lesson_month','lesson_date','role_category','role_subcategory',
                        'role_status','attendance_status','attendance_status_text','lesson_type','is_shift_priority','shift_fee_yen','is_future','is_employee']]

def shift_metrics(g):
    st = g['attendance_status']
    return pd.Series({
        'shift_registered': len(g),
        'shift_registered_future': int(g['is_future'].sum()),
        'shift_confirmed': int(st.isin([1,2,3,5]).sum()),       # 登録のうちご自愛(シフトカット)されていないもの
        'shift_attended': int((st == 2).sum()),                  # 実施
        'shift_min_guarantee': int((st == 5).sum()),             # 最低保証（開催なし）
        'shift_jiai_cancel': int((st == 4).sum()),               # ご自愛 = シフトカット
        'shift_absent': int((st == 3).sum()),                    # 欠席
        'shift_unresolved_past': int(((st == 1) & (~g['is_future'])).sum()),  # 実施日超過だが出欠未反映
        'shift_priority': int((g['is_shift_priority'].str.lower() == 'true').sum()),
        'shift_role_planner': int((g['role_status'] == 'プランナー').sum()),
        'shift_role_leader': int((g['role_status'] == 'リーダー').sum()),
        'shift_role_facilitator': int((g['role_status'] == 'ファシリテーター').sum()),
        'shift_role_support': int((g['role_status'] == 'サポートプランナー').sum()),
        'shift_role_other': int((~g['role_status'].isin(['プランナー','リーダー','ファシリテーター','サポートプランナー'])).sum()),
        'shift_fee_yen': float(g['shift_fee_yen'].sum()),
        'shift_fee_leader_facil_support_yen': float(g.loc[g['role_status'].isin(['リーダー','ファシリテーター','サポートプランナー']), 'shift_fee_yen'].sum()),
        'shift_fee_min_guarantee_yen': float(g.loc[st == 5, 'shift_fee_yen'].sum()),
        'shift_fee_planner_base_yen': float(g.loc[(g['role_status'] == 'プランナー') & (st == 2), 'shift_fee_yen'].sum()),
    })

def participant_metrics(g):
    p = g[g['has_participant']]
    lead = p[p['is_lead_row']]
    asst = p[p['is_assist_row']]
    out = {
        'participants_all': p['trial_lesson_reservation_id'].nunique(),
        'participants_lead': lead['trial_lesson_reservation_id'].nunique(),
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
        # アシスト成果（参考: 誰の成果かは制度設計次第）
        'gross_assist': int(asst['is_conversion_gross'].sum()),
        'valid_assist': int(asst['is_valid_conversion'].sum()),
        'coolingoff_assist': int(asst['is_coolingoff'].sum()),
        # 全行ベース（GCアシスト含む・参考値/既存集計に近い）
        'gross_all': int(p['is_conversion_gross'].sum()),
        'valid_all': int(p['is_valid_conversion'].sum()),
        'coolingoff_all': int(p['is_coolingoff'].sum()),
        # 加重（VCVモニタリング方式: 1on1=1.0, GCリード=0.7, GCアシスト=0.3）
        'weighted_valid': float((lead['is_valid_conversion'] * np.where(lead['is_1on1'], 1.0, 0.7)).sum() + (asst['is_valid_conversion'] * 0.3).sum()),
        'weighted_vcv': float((lead['virtual_cv'].fillna(0) * np.where(lead['is_1on1'], 1.0, 0.7)).sum() + (asst['virtual_cv'].fillna(0) * 0.3).sum()),
        'vcv_sum_lead': float(lead['virtual_cv'].fillna(0).sum()),
        # 報酬（成約インセンティブ: BQ算出。アシスト分含む）
        'incentive_yen_payable': float(p['incentive_yen_payable'].fillna(0).sum()),
        'incentive_yen_lead': float(lead['incentive_yen_payable'].fillna(0).sum()),
        'incentive_yen_assist': float(asst['incentive_yen_payable'].fillna(0).sum()),
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

def add_rates(t):
    def rate(n, d): return np.where(d > 0, n / d, np.nan)
    t['same_day_rate_lead'] = rate(t['same_day_gross_lead'], t['participants_lead'])
    t['gross_rate_lead'] = rate(t['gross_lead'], t['participants_lead'])
    t['coolingoff_rate_lead'] = rate(t['coolingoff_lead'], t['gross_lead'])
    t['valid_rate_lead'] = rate(t['valid_lead'], t['participants_lead'])
    t['valid_rate_all'] = rate(t['valid_all'], t['participants_all'])
    t['cancel_rate_shift'] = rate(t['shift_jiai_cancel'] + t['shift_absent'], t['shift_registered'])
    t['jiai_rate_shift'] = rate(t['shift_jiai_cancel'], t['shift_registered'])
    paid_col = 'incentive_yen_payable_paid' if 'incentive_yen_payable_paid' in t.columns else 'incentive_yen_payable'
    t['total_reward_yen'] = t['shift_fee_yen'] + t[paid_col]
    t['valid_per_shift'] = rate(t['valid_lead'], t['shift_attended'])
    t['valid_per_100_shift'] = t['valid_per_shift'] * 100
    t['participants_per_shift'] = rate(t['participants_lead'], t['shift_attended'])
    t['reward_per_valid_yen'] = rate(t['total_reward_yen'], t['valid_lead'])
    t['valid_per_reward_1man_yen'] = rate(t['valid_lead'] * 10000, t['total_reward_yen'])
    t['first_month_revenue_per_reward'] = rate(t['first_month_revenue_valid_lead'], t['total_reward_yen'])
    t['entrance_fee_per_reward'] = rate(t['entrance_fee_valid_lead'], t['total_reward_yen'])
    t['weighted_vcv_achievement'] = rate(t['weighted_valid'], t['weighted_vcv'])
    t['sample_insufficient_flag'] = t['participants_lead'] < MIN_SAMPLE_PARTICIPANTS
    return t

# ---------- ②個人×月×役割 ----------
keys = ['planner_id','planner_name','lesson_month','role_category','role_subcategory']
sm = shift_tbl.groupby(keys, dropna=False).apply(shift_metrics, include_groups=False).reset_index()
pm = df.groupby(keys, dropna=False).apply(participant_metrics, include_groups=False).reset_index()
ind = sm.merge(pm, on=keys, how='outer').fillna(0)
ind['is_employee'] = ind['role_category'].eq('SE') | ind['role_subcategory'].isin(['その他_generalist(社員)','その他_社員等(メタなし/未設定)','その他_CMM(拠点)'])
ind['incentive_yen_payable_paid'] = np.where(ind['is_employee'], 0, ind['incentive_yen_payable'])
ind = add_rates(ind)
# 兼務・役割変更フラグ
multi = ind.groupby(['planner_id','lesson_month'])['role_category'].nunique().rename('n_roles_in_month').reset_index()
ind = ind.merge(multi, on=['planner_id','lesson_month'], how='left')
ind['role_changed_in_month'] = ind['n_roles_in_month'] > 1
ind['reward_note'] = np.where(ind['is_employee'], '社員（給与）: シフト報酬/成約インセンティブは業務委託レートを適用せず0。成約インセンティブ相当額はincentive_yen_payableに参考計上', '')
ind = ind.sort_values(['lesson_month','role_category','planner_id'])

# ---------- 個人別 期間合計（2026-07以降 と 2026全期間）----------
def period_summary(mask, label):
    sub = df[mask]; sub_shift = shift_tbl[shift_tbl['shift_key'].isin(sub['shift_key'])]
    k = ['planner_id','planner_name','role_category']
    a = sub_shift.groupby(k, dropna=False).apply(shift_metrics, include_groups=False).reset_index()
    b = sub.groupby(k, dropna=False).apply(participant_metrics, include_groups=False).reset_index()
    t = a.merge(b, on=k, how='outer').fillna(0)
    emp_ids = set(ind.loc[ind['is_employee'], 'planner_id'])
    t['is_employee'] = t['role_category'].eq('SE') | (t['planner_id'].isin(emp_ids) & t['role_category'].eq('その他'))
    t['incentive_yen_payable_paid'] = np.where(t['is_employee'], 0, t['incentive_yen_payable'])
    t = add_rates(t)
    t['months_active'] = sub[sub['attendance_status'] == 2].groupby(k)['lesson_month'].nunique().reindex(pd.MultiIndex.from_frame(t[k])).values
    t['period'] = label
    return t
ind_period = pd.concat([period_summary(df['lesson_month'] >= '2026-07', '2026-07以降'),
                        period_summary(df['lesson_month'] >= '2026-01', '2026-01以降')], ignore_index=True)

# ---------- ③役割別月次 ----------
def role_month(t_ind, group_cols):
    rows = []
    for key, g in t_ind.groupby(group_cols, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        r = dict(zip(group_cols, key))
        r['headcount_registered'] = g['planner_id'].nunique()
        r['headcount_active'] = g.loc[g['shift_attended'] > 0, 'planner_id'].nunique()
        for c in ['shift_registered','shift_confirmed','shift_attended','shift_min_guarantee','shift_jiai_cancel','shift_absent','shift_unresolved_past','shift_registered_future',
                  'participants_all','participants_lead','lead_1on1','lead_gc','assist_gc','lounge',
                  'same_day_gross_lead','later_gross_lead','gross_lead','coolingoff_lead','valid_lead','same_day_valid_lead','later_valid_lead',
                  'gross_assist','valid_assist','gross_all','valid_all','coolingoff_all','weighted_valid','weighted_vcv','vcv_sum_lead',
                  'shift_fee_yen','shift_fee_planner_base_yen','shift_fee_leader_facil_support_yen','shift_fee_min_guarantee_yen',
                  'incentive_yen_payable','incentive_yen_payable_paid','total_reward_yen',
                  'entrance_fee_valid_lead','plan_amount_valid_lead','first_month_revenue_valid_lead',
                  'lead_subsidy_target','lead_valid_subsidy_target','lead_difficulty_4_5_6','lead_valid_difficulty_4_5_6','lead_utm_other','lead_valid_utm_other']:
            r[c] = g[c].sum()
        # 個人分布（月間リード参加者30以上の個人のみで四分位）
        q = g[(g['participants_lead'] >= MIN_SAMPLE_PARTICIPANTS)]
        for col, name in [('valid_rate_lead','valid_rate'), ('shift_attended','shifts'), ('valid_lead','valid_cnt'), ('valid_per_shift','valid_per_shift'), ('total_reward_yen','reward')]:
            s = q[col].replace([np.inf, -np.inf], np.nan).dropna()
            r[f'{name}_n'] = len(s)
            for stat, fn in [('min', 'min'), ('p25', 0.25), ('median', 0.5), ('p75', 0.75), ('max', 'max')]:
                r[f'{name}_{stat}'] = (getattr(s, fn)() if isinstance(fn, str) else s.quantile(fn)) if len(s) else np.nan
        rows.append(r)
    t = pd.DataFrame(rows)
    t = add_rates(t)
    t['subsidy_valid_rate_lead'] = np.where(t['lead_subsidy_target'] > 0, t['lead_valid_subsidy_target'] / t['lead_subsidy_target'], np.nan)
    t['difficulty456_valid_rate_lead'] = np.where(t['lead_difficulty_4_5_6'] > 0, t['lead_valid_difficulty_4_5_6'] / t['lead_difficulty_4_5_6'], np.nan)
    t['utm_other_valid_rate_lead'] = np.where(t['lead_utm_other'] > 0, t['lead_valid_utm_other'] / t['lead_utm_other'], np.nan)
    t['shifts_per_active_planner'] = np.where(t['headcount_active'] > 0, t['shift_attended'] / t['headcount_active'], np.nan)
    t['valid_per_active_planner'] = np.where(t['headcount_active'] > 0, t['valid_lead'] / t['headcount_active'], np.nan)
    t['reward_per_active_planner'] = np.where(t['headcount_active'] > 0, t['total_reward_yen'] / t['headcount_active'], np.nan)
    return t

role_m = role_month(ind, ['lesson_month','role_category']).sort_values(['lesson_month','role_category'])
role_sub_m = role_month(ind, ['lesson_month','role_category','role_subcategory']).sort_values(['lesson_month','role_category','role_subcategory'])
ind_p_role = ind_period.copy()
role_period = pd.concat([role_month(ind_p_role[ind_p_role['period'] == p], ['role_category']).assign(period=p) for p in ['2026-07以降','2026-01以降']], ignore_index=True)

# ---------- 上位者（成約率とシフト数の両方が高い）----------
def top_performers(t, label):
    out = []
    for role, g in t.groupby('role_category'):
        q = g[g['participants_lead'] >= MIN_SAMPLE_PARTICIPANTS].copy()
        if len(q) == 0: continue
        med_rate = q['valid_rate_lead'].median(); med_shift = q['shift_attended'].median()
        q['role_median_valid_rate'] = med_rate; q['role_median_shift_attended'] = med_shift
        q['is_top_both'] = (q['valid_rate_lead'] >= med_rate) & (q['shift_attended'] >= med_shift)
        q['rank_valid_rate_in_role'] = q['valid_rate_lead'].rank(ascending=False, method='min')
        q['rank_shift_in_role'] = q['shift_attended'].rank(ascending=False, method='min')
        q['rank_valid_cnt_in_role'] = q['valid_lead'].rank(ascending=False, method='min')
        q['period'] = label
        out.append(q)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
top_period = top_performers(ind_period[ind_period['period'] == '2026-07以降'], '2026-07以降')
top_month = pd.concat([top_performers(ind[ind['lesson_month'] == m], m) for m in sorted(ind['lesson_month'].unique())], ignore_index=True)

# ---------- 検算表（既存集計「その他」との比較: 現在値のplanner_term/grade ベース, role_status=プランナー）----------
chk = df[(df['current_planner_term'] == 'SE') & (df['role_status'] == 'プランナー') & (df['has_participant'])]
recon_current = chk.groupby('lesson_month').agg(rows=('trial_lesson_reservation_id','size'), participants=('trial_lesson_reservation_id','nunique'),
                                               valid=('is_valid_conversion','sum'), gross=('is_conversion_gross','sum'), coolingoff=('is_coolingoff','sum')).reset_index()
recon_current['valid_rate'] = recon_current['valid'] / recon_current['participants']
recon_current['basis'] = '現在値planner_term=SE & role_status=プランナー（既存集計の再現）'
chk2 = df[(df['role_category'] == 'SE') & (df['has_participant'])]
recon_asof = chk2.groupby('lesson_month').agg(rows=('trial_lesson_reservation_id','size'), participants=('trial_lesson_reservation_id','nunique'),
                                             valid=('is_valid_conversion','sum'), gross=('is_conversion_gross','sum'), coolingoff=('is_coolingoff','sum')).reset_index()
recon_asof['valid_rate'] = recon_asof['valid'] / recon_asof['participants']
recon_asof['basis'] = '本抽出: 実施日時点role_category=SE（全シフト役割・GCアシスト含む）'
chk3 = df[(df['role_category'] == 'SE') & (df['is_lead_row'])]
recon_lead = chk3.groupby('lesson_month').agg(rows=('trial_lesson_reservation_id','size'), participants=('trial_lesson_reservation_id','nunique'),
                                             valid=('is_valid_conversion','sum'), gross=('is_conversion_gross','sum'), coolingoff=('is_coolingoff','sum')).reset_index()
recon_lead['valid_rate'] = recon_lead['valid'] / recon_lead['participants']
recon_lead['basis'] = '本抽出: role_category=SE & リード（1on1/GCリード）のみ'
ref = pd.DataFrame({'lesson_month':['2026-07','2026-08','2026-09'],'participants':[793,669,191],'valid':[310,264,59],'valid_rate':[0.3909,0.3946,0.3089],'basis':'依頼記載の既存集計「その他」参考値'})
recon = pd.concat([ref, recon_current, recon_asof, recon_lead], ignore_index=True)

# ---------- 対象者リスト照合 ----------
se_list = ['三島加菜','野間香南子','平林花織','平川美希','安達美里','吉野由佳','平原奈津子','大場史子','井野麗菜','平川晴美','中村江里','木村駿','松本紗彩','佐藤智子','五師光葉']
pl = df.groupby(['planner_id','planner_name']).agg(first_lesson=('lesson_date','min'), last_lesson=('lesson_date','max'),
        roles_asof=('role_subcategory', lambda s: '/'.join(sorted(set(s)))), current_term=('current_planner_term','first'), current_grade=('current_planner_grade','first'),
        shifts_attended=('attendance_status', lambda s: int((s==2).sum())), rows=('shift_key','size')).reset_index()
pl['name_norm'] = pl['planner_name'].str.replace(r'\s+','',regex=True)
pl['in_se_list'] = pl['name_norm'].isin(se_list)
pl['ever_SE_asof'] = pl['roles_asof'].str.contains('SE')
pl['flag'] = np.select([pl['in_se_list'] & ~pl['ever_SE_asof'], ~pl['in_se_list'] & pl['ever_SE_asof'], pl['planner_name'].str.contains(r'\s', regex=True)],
                       ['リストにあるがSE判定なし','SE判定だがリストにない','氏名に空白（表記揺れ候補）'], '')
missing = [n for n in se_list if n not in set(pl['name_norm'])]
dup_names = pl[pl.duplicated('name_norm', keep=False)]
roster = pl.sort_values(['in_se_list','planner_name'], ascending=[False, True])

# ---------- 役割変更履歴（対象期間中に区分が変わった人）----------
chg = df.groupby(['planner_id','planner_name','role_category','role_subcategory','asof_generation','asof_career_path','asof_effective_at']).agg(
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
    pd.DataFrame({'項目':['データ取得日(JST)','対象期間','明細行数','サンプル不足閾値(月間リード参加者数)','SEリスト未検出氏名'],
                  '値':[ASOF_DATE, f"{df['lesson_date'].min().date()}〜{df['lesson_date'].max().date()}", len(df), MIN_SAMPLE_PARTICIPANTS, ', '.join(missing) or 'なし']}).to_excel(xw, sheet_name='README', index=False)
    role_m.to_excel(xw, sheet_name='③役割別月次', index=False)
    role_sub_m.to_excel(xw, sheet_name='③b役割サブ区分別月次', index=False)
    role_period.to_excel(xw, sheet_name='③c役割別期間合計', index=False)
    ind.to_excel(xw, sheet_name='②個人×月', index=False)
    ind_period.to_excel(xw, sheet_name='②b個人×期間', index=False)
    top_period.to_excel(xw, sheet_name='④上位者(7月以降)', index=False)
    top_month.to_excel(xw, sheet_name='④b上位者(月次)', index=False)
    recon.to_excel(xw, sheet_name='⑤検算', index=False)
    roster.to_excel(xw, sheet_name='⑥対象者照合', index=False)
    chg_multi.to_excel(xw, sheet_name='⑦役割変更履歴', index=False)
    dup_names.to_excel(xw, sheet_name='⑥b同名複数ID', index=False)
    # 明細は行数が多いので7月以降のみExcelに（全期間はCSV）
    df[df['lesson_month'] >= '2026-07'].drop(columns=['is_future','has_participant','is_lead_row','is_assist_row','is_1on1','is_gc_lead','is_lounge','is_employee','is_trial_membership_b']).to_excel(xw, sheet_name='①明細(2026-07以降)', index=False)

print('rows', len(df), '| individual-month', len(ind), '| role-month', len(role_m), '| missing SE names:', missing)
print(role_m[['lesson_month','role_category','headcount_active','shift_attended','participants_lead','valid_lead','valid_rate_lead','total_reward_yen']].to_string())
print(recon.to_string())
