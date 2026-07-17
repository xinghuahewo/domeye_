import os
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd



def plot_features(df, title, column, interval=1):
    """绘制特征图
    Args:
        df (pd.DataFrame): 包含数据的DataFrame 从数据库中获取的数据
        title (str): 图表标题
        interval (int): 时间间隔
    """
    # 转换时间格式
    df['t'] = pd.to_datetime(df['t'])

    sns.set_style('whitegrid')
    fig, ax1 = plt.subplots(figsize=(12, 4), dpi=150)


    # 主轴：Withdraw Num
    ax1.plot(df['t'], df[column], 'o-', color='blue', label=column, markersize=3, linewidth=1.2)
    ax1.set_ylabel(column, fontsize=12, color='blue')
    ax1.tick_params(axis='y', labelcolor='blue')
    # 添加健壮性检查，避免NaN或Inf值
    max_withdraw = df[column].max() if not df[column].empty and df[column].notna().any() else 0
    ax1.set_ylim(0, max(max_withdraw, 200))
    ax1.spines['bottom'].set_position(('outward', 6))  # 时间轴往下移
    
    # 设置Y轴格式为完整数字，不使用科学计数法
    from matplotlib.ticker import FuncFormatter
    def format_func(x, p):
        return f'{int(x)}'
    ax1.yaxis.set_major_formatter(FuncFormatter(format_func))


    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=interval))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    ax1.tick_params(axis='x', labelrotation=0, labelsize=10)  # 取消倾斜

    # 设置标题和标签
    ax1.set_title(title, fontsize=14)
    ax1.set_xlabel("Time")

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()

    ax1.grid(True, linestyle='--', alpha=0.3)
    plt.tight_layout()
    plt.savefig(title, dpi=300, bbox_inches='tight')


import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.feature_country import select_country_feature_db

from database.as_outage import select_as_outage_by_interval

def get_as_outage_by_interval(conn, source, start_time, end_time, country):
    """
    获取与指定时间范围重叠的AS中断记录。 可以跨越多张表
    Args:
        conn: 数据库连接
        source: 数据源
        start_time: 开始时间
        end_time: 结束时间
        country: 国家
        table_name: 表名
    Returns:
        pd.DataFrame: 包含 'asn', 's_time', 'e_time' 的 DataFrame，表示AS中断记录。
    """
    # 1. 获取表名列表
    tables = ['as_outage_202506_test']

    # 2. 获取每个表中与时间范围重叠的AS中断记录
    all_as_outages = []
    for table in tables:
        as_outages = select_as_outage_by_interval(conn, source, start_time, end_time, country, table)
        if len(as_outages) > 0:
            all_as_outages.extend(as_outages)

    # 3. 将结果转换为DataFrame
    if all_as_outages:
        df = pd.DataFrame(all_as_outages, columns=['asn', 's_time', 'e_time'])
        return df
    else:
        return pd.DataFrame(columns=['asn', 's_time', 'e_time'])


def deal_outage(df, type, start_time, end_time, prefixes, interval_minutes=3):
    """
    根据中断事件的 DataFrame，计算每个时间间隔内的并发中断前缀数量。
    Args:
        df (pd.DataFrame): 包含 'prefix' 或 'asn', 's_time', 'e_time' 的 DataFrame。
        type (str): 中断类型，'prefix' 或 'asn'。
        start_time (str): 分析的开始时间。
        end_time (str): 分析的结束时间。
        prefixes (list): 前缀列表。
        interval_minutes (int): 时间间隔（分钟）。

    Returns:
        # pd.DataFrame: 包含 'time_slot' 和 'outage_count' 的结果 DataFrame。
        [
            {
                'time_slot': '时间',
                'outage_count': '中断数'
            }
        ]
    """
    if type == 'prefix':
        # 去除细路由，只保留粗路由
        df = df[df['prefix'].isin(prefixes)]
        df = df.drop_duplicates(subset=['prefix', 's_time']).copy()
    
    
    # 1. 生成时间序列
    time_slots = pd.date_range(start=start_time, end=end_time, freq=f'{interval_minutes}min')
    time_df = pd.DataFrame({'time_slot': time_slots})

    if df.empty:
        # 如果没有中断数据，直接返回带0计数的时间序列
        time_df['outage_count'] = 0
        # 修改成[]
        result = []
        for index, row in time_df.iterrows():
            result.append({
                'time_slot': str(row['time_slot']),
                'outage_count': 0
            })
        return result

    # 2. 准备中断数据
    df['s_time'] = pd.to_datetime(df['s_time'])
    df['e_time'] = pd.to_datetime(df['e_time'])
    df = df.drop_duplicates(subset=[type, 's_time', 'e_time']).copy()

    # 3. 使用交叉合并来创建所有 (时间点, 中断) 的组合
    merged_df = time_df.merge(df, how='cross')

    # 4. 筛选出在每个 time_slot 处于活动状态的中断
    is_active = (merged_df['s_time'] <= merged_df['time_slot']) & \
                ((merged_df['e_time'] > merged_df['time_slot']) | pd.isna(merged_df['e_time']))
    
    active_outages = merged_df[is_active]

    # 5. 按时间点分组并计算唯一前缀的数量
    if not active_outages.empty:
        outage_counts = active_outages.groupby('time_slot')[type].nunique().reset_index()
        outage_counts.rename(columns={type: 'outage_count'}, inplace=True)
    else:
        outage_counts = pd.DataFrame({'time_slot': [], 'outage_count': []})
        outage_counts['time_slot'] = pd.to_datetime(outage_counts['time_slot'])

    # 6. 将计数与完整的时间序列合并，以包含中断数为0的时间点
    final_df = time_df.merge(outage_counts, on='time_slot', how='left').fillna(0)
    final_df['outage_count'] = final_df['outage_count'].astype(int)
    return final_df


def plot_outage(df, type, start_time, end_time, title, interval_minutes=3):
        """
        绘制前缀中断数量随时间的变化图
        """
        # 对中断事件数据进行处理
        # 转换时间列
        df['time_slot'] = pd.to_datetime(df['time_slot'])
        # df.to_csv('/home/bgpdata/bgpcore/0520/temp/iran_prefix_outage.csv', index=False)
        
        # 获取start_time时间点的中断数量
        start_time_dt = pd.to_datetime(start_time)
        start_outage_count = df[df['time_slot'] == start_time_dt]['outage_count'].iloc[0] if len(df[df['time_slot'] == start_time_dt]) > 0 else 0
        
        # 设置绘图风格
        sns.set_style('whitegrid')
        fig, ax1 = plt.subplots(figsize=(12, 4), dpi=150)
        
        # 绘制折线图
        ax1.plot(df['time_slot'], df['outage_count'], 'o-', color='blue', label='Outage Number', markersize=3, linewidth=1.2)
        ax1.set_ylabel("Outage Number", fontsize=12, color='blue')
        ax1.tick_params(axis='y', labelcolor='blue')
        ax1.set_ylim(0, df['outage_count'].max() * 1.1 if df['outage_count'].max() > 0 else 10)
        ax1.spines['bottom'].set_position(('outward', 6))
        
        # 标注start_time时间点
        ax1.axvline(x=start_time_dt, color='red', linestyle='--', alpha=0.7, label=f'Start Time: {start_outage_count} outages')
        if start_outage_count > 0:
            ax1.annotate(f'{start_outage_count}', 
                        xy=(start_time_dt, start_outage_count), 
                        xytext=(10, 10), 
                        textcoords='offset points',
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='red', alpha=0.3),
                        arrowprops=dict(arrowstyle='->', color='red'))
        
        # 设置x轴格式
        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=24))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        ax1.tick_params(axis='x', labelrotation=0, labelsize=10)
        
        # 设置标题和标签
        ax1.set_title(f"{title} Outage Number", fontsize=14)
        ax1.set_xlabel("Time")
        ax1.grid(True, linestyle='--', alpha=0.3)
        ax1.legend()
        
        # 调整布局
        plt.tight_layout()
        save_path = f'{title}.png'
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        if type == 'prefix':
            print(f"前缀中断统计: start_time ({start_time}) 时刻有 {start_outage_count} 个前缀处于中断状态")
        elif type == 'asn':
            print(f"AS中断统计: start_time ({start_time}) 时刻有 {start_outage_count} 个AS处于中断状态")


# from database.utils import get_conn
# from config.database import DATABASE, HOST, USER, PASSWORD, PORT 
# conn = get_conn(DATABASE, USER, PASSWORD, HOST, PORT)
# df = get_as_outage_by_interval(conn, 'r', '2025-06-12 00:00:00', '2025-06-20 00:00:00', '伊朗')
# print(df.head())
# result = deal_outage(df, 'asn', '2025-06-12 00:00:00', '2025-06-20 00:00:00', [], interval_minutes=3)

# plot_outage(result, 'asn', '2025-06-12 00:00:00', '2025-06-20 00:00:00', 'Iran AS Outage')

# plot_features(df, 'Iran v4ip_num', '', interval=24)  # 24小时间隔

import pandas  as pd
df = pd.read_csv('/home/bgpdata/Domeye/backend/tests/afg_boundary_as_over_time.csv')
df.to_excel('/home/bgpdata/Domeye/backend/tests/afg_boundary_as_over_time.xlsx', index=False)