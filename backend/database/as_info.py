import psycopg2
import pandas as pd
import time
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logger import database_logger
#### TODO: 已弃用



def create_as_info_table(conn):
    """
    创建as_info表
    """
    cursor = conn.cursor()
    sql = """
        CREATE TABLE if not exists as_info (
            asn text,
            as_name text,
            as_country_cn text,
            org_name_cn text,
            v4Prefixes_num int,
            v6Prefixes_num int,
            v4Peer_num int,
            v6Peer_num int,
            PRIMARY KEY (asn)
        );
    """
    try:
        cursor.execute(sql)
        conn.commit()
        
        # 创建优化索引
        try:
            # 按国家查询优化
            cursor.execute("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_as_info_country 
                ON as_info (as_country_cn) WHERE as_country_cn IS NOT NULL
            """)
            
            # 按前缀数量查询优化（用于统计分析）
            cursor.execute("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_as_info_v4prefixes 
                ON as_info (v4Prefixes_num DESC) WHERE v4Prefixes_num IS NOT NULL
            """)
            
            cursor.execute("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_as_info_v6prefixes 
                ON as_info (v6Prefixes_num DESC) WHERE v6Prefixes_num IS NOT NULL
            """)
            
            # 按组织名称查询优化
            cursor.execute("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_as_info_org_name 
                ON as_info USING GIN (to_tsvector('simple', org_name_cn)) 
                WHERE org_name_cn IS NOT NULL
            """)
            
            # 按AS名称查询优化
            cursor.execute("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_as_info_as_name 
                ON as_info USING GIN (to_tsvector('simple', as_name)) 
                WHERE as_name IS NOT NULL
            """)
            
            conn.commit()
            database_logger.info(f'Successfully created optimized indexes for table as_info')
            
        except Exception as e:
            database_logger.warning(f'Failed to create some indexes for table as_info: {e}')
            conn.rollback()
        
    except Exception as e:
        database_logger.error(f"创建as_info表失败: {e}")
    finally:
        cursor.close()

def insert_as_info(conn, asn, country, as_name, org_name, as_rank, as_type, v4Prefixes_num, v6Prefixes_num, v4Peer_num, v6Peer_num):
    """
    插入as_info表
    """
    cursor = conn.cursor()
    sql = """
        INSERT INTO as_info (asn, as_country_cn, as_name, org_name_cn, v4Prefixes_num, v6Prefixes_num, v4Peer_num, v6Peer_num)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.execute(sql, (asn, country, as_name, org_name, v4Prefixes_num, v6Prefixes_num, v4Peer_num, v6Peer_num))
        conn.commit()
    except Exception as e:
        database_logger.error(f"插入as_info表失败: {e}")
    finally:
        cursor.close()

def insert_as_info_batch(conn, as_info_list):
    """
    批量插入as_info表
    """
    cursor = conn.cursor()
    sql = """
        INSERT INTO as_info (asn, as_country_cn, as_name, org_name_cn, v4Prefixes_num, v6Prefixes_num, v4Peer_num, v6Peer_num)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.executemany(sql, as_info_list)
        conn.commit()
    except Exception as e:
        database_logger.error(f"批量插入as_info表失败: {e}")
    finally:
        cursor.close()

def get_existing_as_info(conn):
    """
    获取数据库中已存在的AS信息
    
    Args:
        conn: 数据库连接
        
    Returns:
        set: 已存在的AS号集合
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT asn FROM as_info")
        existing_as = {row[0] for row in cursor.fetchall()}
        database_logger.info(f"数据库中已存在 {len(existing_as)} 个AS记录")
        return existing_as
    except Exception as e:
        database_logger.error(f"获取现有AS信息失败: {e}")
        return set()
    finally:
        cursor.close()

def upsert_as_info_batch(conn, as_info_list):
    """
    批量UPSERT（插入或更新）AS信息
    
    Args:
        conn: 数据库连接
        as_info_list: [(asn, country), ...] 格式的AS信息列表
    """
    if not as_info_list:
        database_logger.info("as_info表没有数据需要更新")
        return
        
    cursor = conn.cursor()
    try:
        # 使用PostgreSQL的ON CONFLICT语法实现UPSERT
        sql = """
            INSERT INTO as_info (asn, as_country_cn, as_name, org_name_cn, v4Prefixes_num, v6Prefixes_num, v4Peer_num, v6Peer_num)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asn)
            DO UPDATE SET as_country_cn = EXCLUDED.as_country_cn, as_name = EXCLUDED.as_name, org_name_cn = EXCLUDED.org_name_cn, v4Prefixes_num = EXCLUDED.v4Prefixes_num, v6Prefixes_num = EXCLUDED.v6Prefixes_num, v4Peer_num = EXCLUDED.v4Peer_num, v6Peer_num = EXCLUDED.v6Peer_num
        """
        cursor.executemany(sql, as_info_list)
        conn.commit()
        database_logger.info(f"成功UPSERT {len(as_info_list)} 条AS信息记录")
    except Exception as e:
        database_logger.error(f"批量UPSERT AS信息失败: {e}")
        conn.rollback()
    finally:
        cursor.close()

def update_as_info_from_file(conn, as_info_file, force_update=False):
    """
    从AS信息文件更新数据库中的as_info表
    
    Args:
        conn: 数据库连接
        as_info_file: AS信息文件路径
        force_update: 是否强制更新所有记录，默认False只更新缺失的
    """
    print(f"开始从文件更新AS信息: {as_info_file}")
    start_time = time.time()
    
    try:
        # 🔄 1. 读取AS信息文件
        print("正在读取AS信息文件...")
        # 只读取需要的列，提高性能
        df = pd.read_csv(
            as_info_file, 
            keep_default_na=False, 
            usecols=['asn', 'as_country_cn', 'as_name', 'org_name_cn', 'v4Prefixes_num', 'v6Prefixes_num', 'v4Peer_num', 'v6Peer_num'],
            dtype={'asn': str, 'as_country_cn': str, 'as_name': str, 'org_name_cn': str, 'v4Prefixes_num': int, 'v6Prefixes_num': int, 'v4Peer_num': int, 'v6Peer_num': int}
        )
        
        # 去重并处理数据
        df.drop_duplicates(subset=['asn'], keep='first', inplace=True)
        df = df[df['asn'].str.strip() != '']  # 移除空AS号
        df = df[df['as_country_cn'].str.strip() != '']  # 移除空国家
        
        print(f"文件中包含 {len(df)} 条有效的AS-国家映射记录")
        
        if not force_update:
            # 🔄 2. 获取现有AS记录
            existing_as = get_existing_as_info(conn)
            
            # 🔄 3. 筛选出需要插入的新记录
            df_new = df[~df['asn'].isin(existing_as)]
            print(f"发现 {len(df_new)} 条新的AS记录需要插入")
            
            if len(df_new) == 0:
                print("所有AS记录已存在，无需更新")
                return
                
            # 准备插入数据
            as_info_list = [(row['asn'], row['as_country_cn'], row['as_name'], row['org_name_cn'], row['v4Prefixes_num'], row['v6Prefixes_num'], row['v4Peer_num'], row['v6Peer_num']) for _, row in df_new.iterrows()]
        else:
            # 强制更新模式：更新所有记录
            print("强制更新模式：将更新所有AS记录")
            as_info_list = [(row['asn'], row['as_country_cn'], row['as_name'], row['org_name_cn'], row['v4Prefixes_num'], row['v6Prefixes_num'], row['v4Peer_num'], row['v6Peer_num']) for _, row in df.iterrows()]
        
        # 🔄 4. 批量插入/更新
        if as_info_list:
            print(f"开始批量{'更新' if force_update else '插入'} {len(as_info_list)} 条记录...")
            
            # 分批处理，避免内存问题
            batch_size = 5000
            for i in range(0, len(as_info_list), batch_size):
                batch = as_info_list[i:i+batch_size]
                if force_update:
                    upsert_as_info_batch(conn, batch)
                else:
                    insert_as_info_batch(conn, batch)
                print(f"已处理 {min(i+batch_size, len(as_info_list))}/{len(as_info_list)} 条记录")
        
        end_time = time.time()
        print(f"AS信息更新完成！耗时: {end_time - start_time:.2f}秒")
        
    except Exception as e:
        print(f"从文件更新AS信息时发生错误: {e}")
        import traceback
        traceback.print_exc()

def get_as_info_statistics(conn):
    """
    获取as_info表的统计信息
    
    Args:
        conn: 数据库连接
        
    Returns:
        dict: 包含统计信息的字典
    """
    cursor = conn.cursor()
    try:
        # 总记录数
        cursor.execute("SELECT COUNT(*) FROM as_info")
        total_count = cursor.fetchone()[0]
        
        # 按国家统计
        cursor.execute("""
            SELECT as_country_cn, COUNT(*) as count 
            FROM as_info 
            GROUP BY as_country_cn 
            ORDER BY count DESC 
            LIMIT 10
        """)
        top_countries = cursor.fetchall()
        
        # 空国家记录数
        cursor.execute("SELECT COUNT(*) FROM as_info WHERE as_country_cn = '' OR as_country_cn IS NULL")
        empty_country_count = cursor.fetchone()[0]
        
        stats = {
            'total_records': total_count,
            'top_countries': top_countries,
            'empty_country_records': empty_country_count
        }
        
        print(f"=== AS信息表统计 ===")
        print(f"总记录数: {total_count}")
        print(f"空国家记录数: {empty_country_count}")
        print(f"前10个国家的AS数量:")
        for country, count in top_countries:
            print(f"  {country}: {count}")
        
        return stats
        
    except Exception as e:
        print(f"获取AS信息统计失败: {e}")
        return {}
    finally:
        cursor.close()

def init_as_info_table(conn, as_info_file, force_update=False):
    """
    初始化as_info表（创建表并从文件导入数据）
    
    Args:
        conn: 数据库连接
        as_info_file: AS信息文件路径
        force_update: 是否强制更新所有记录
    """
    print("=== 初始化AS信息表 ===")
    
    # 1. 创建表（如果不存在）
    create_as_info_table(conn)
    
    # 2. 从文件更新数据
    update_as_info_from_file(conn, as_info_file, force_update)
    
    # 3. 显示统计信息
    get_as_info_statistics(conn)