import requests
from bs4 import BeautifulSoup
import csv
import re

class RIPERISScraper:
    def __init__(self):
        self.base_url = "https://www.ris.ripe.net/peerlist/all.shtml"
        self.peer_data = []  # 对等体表格数据（含RRC名称）
        self.stats_data = []  # 统计信息数据（含RRC名称）
        self.peer_columns = ["Status", "ASN", "Description", "Address", "v4 Prefixes", "v6 Prefixes"]  # 对等体表格固定列名

    def fetch_data(self):
        """爬取所有RRC的对等体列表和统计信息"""
        try:
            print("开始爬取RIPE RIS数据...")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(self.base_url, headers=headers, timeout=15)
            response.raise_for_status()  # 请求失败时抛出异常
            soup = BeautifulSoup(response.text, 'html.parser')

            # 定位所有RRC的<h2>标签（格式："RRCxx -- ... -- Peer List"）
            rrc_h2s = soup.find_all('h2', string=lambda text: text and text.startswith(' RRC'))
            print(rrc_h2s)

            for h2 in rrc_h2s:
                # 提取RRC名称（如RRC00）
                h2_text = h2.get_text(strip=True)
                rrc_name = h2_text.split(' -- ')[0].strip()
                location = h2_text.split(' -- ')[1].strip()
                print(f"处理RRC: {rrc_name}")

                # 1. 提取对等体表格数据（<h2>后紧跟的<table class="table">）
                peer_table = h2.find_next('table', class_='table')
                if peer_table:
                    rows = peer_table.find_all('tr')
                    # 跳过表头行，处理数据行
                    for row in rows[1:]:
                        cols = row.find_all('td')
                        if len(cols) != 6:  # 确保列数正确（6列）
                            print(f"警告：{rrc_name}的对等体表格行格式异常，跳过该行")
                            continue
                        # 构建行数据（含RRC名称）
                        row_info = {
                            "rrc_name": rrc_name,
                            "Status": cols[0].text.strip(),
                            "ASN": cols[1].text.strip(),
                            "Description": cols[2].text.strip(),
                            "Address": cols[3].text.strip(),
                            "v4 Prefixes": cols[4].text.strip(),
                            "v6 Prefixes": cols[5].text.strip()
                        }
                        self.peer_data.append(row_info)

                # 2. 提取统计信息（<table>后紧跟的<p>标签）
                stats_p = h2.find_next('p')
                if stats_p:
                    stats = {}
                    # 提取位置
                    stats['location'] = location
                    # 提取IPv4全表数量
                    ipv4_match = re.search(r'IPv4 full tables: (\d+)', stats_p.text)
                    stats['ipv4_full_tables'] = ipv4_match.group(1) if ipv4_match else ""
                    # 提取IPv6全表数量
                    ipv6_match = re.search(r'IPv6 full tables: (\d+)', stats_p.text)
                    stats['ipv6_full_tables'] = ipv6_match.group(1) if ipv6_match else ""
                    # 提取总对等体数量
                    total_match = re.search(r'Total peerings: (\d+)', stats_p.text)
                    stats['total_peerings'] = total_match.group(1) if total_match else ""
                    # 提取数据时间
                    time_match = re.search(r'Data source time: (.+)', stats_p.text)
                    stats['data_source_time'] = time_match.group(1).strip() if time_match else ""
                    # 添加RRC名称
                    stats["rrc_name"] = rrc_name
                    self.stats_data.append(stats)

            print(f"对等体表格共提取 {len(self.peer_data)} 条数据")
            print(f"统计信息共提取 {len(self.stats_data)} 条数据")
            return True

        except Exception as e:
            print(f"爬取失败: {str(e)}", exc_info=True)
            return False

    def save_to_csv(self):
        """保存为两个CSV文件：对等体列表 + 统计信息"""
        # 保存对等体表格
        if self.peer_data:
            peer_csv = "rrc_peer_list.csv"
            fieldnames = ["rrc_name"] + self.peer_columns
            with open(peer_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.peer_data)
            print(f"对等体列表已保存至: {peer_csv}")

        # 保存统计信息
        if self.stats_data:
            stats_csv = "rrc_stats.csv"
            fieldnames = ["rrc_name", "location", "ipv4_full_tables", "ipv6_full_tables", "total_peerings", "data_source_time"]
            with open(stats_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.stats_data)
            print(f"统计信息已保存至: {stats_csv}")


import requests
from bs4 import BeautifulSoup
import csv
import re

import requests
from bs4 import BeautifulSoup
import csv
import re

class RIPERISScraper:
    def __init__(self):
        self.base_url = "https://www.ris.ripe.net/peerlist/all.shtml"
        self.peer_data = []  # 对等体表格数据（含RRC名称）
        self.stats_data = []  # 统计信息数据（含RRC名称）
        self.peer_columns = ["Status", "ASN", "Description", "Address", "v4 Prefixes", "v6 Prefixes"]  # 对等体表格固定列名

    def fetch_data(self):
        """爬取所有RRC的对等体列表和统计信息"""
        try:
            print("开始爬取RIPE RIS数据...")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(self.base_url, headers=headers, timeout=15)
            response.raise_for_status()  # 请求失败时抛出异常
            soup = BeautifulSoup(response.text, 'html.parser')

            # 定位所有RRC的<h2>标签（格式："RRCxx -- ... -- Peer List"）
            rrc_h2s = soup.find_all('h2', string=lambda text: text and text.startswith(' RRC'))
            print(rrc_h2s)

            for h2 in rrc_h2s:
                # 提取RRC名称（如RRC00）
                h2_text = h2.get_text(strip=True)
                rrc_name = h2_text.split(' -- ')[0].strip()
                location = h2_text.split(' -- ')[1].strip()
                print(f"处理RRC: {rrc_name}")

                # 1. 提取对等体表格数据（<h2>后紧跟的<table class="table">）
                peer_table = h2.find_next('table', class_='table')
                if peer_table:
                    rows = peer_table.find_all('tr')
                    # 跳过表头行，处理数据行
                    for row in rows[1:]:
                        cols = row.find_all('td')
                        if len(cols) != 6:  # 确保列数正确（6列）
                            print(f"警告：{rrc_name}的对等体表格行格式异常，跳过该行")
                            continue
                        # 构建行数据（含RRC名称）
                        row_info = {
                            "rrc_name": rrc_name,
                            "Status": cols[0].text.strip(),
                            "ASN": cols[1].text.strip(),
                            "Description": cols[2].text.strip(),
                            "Address": cols[3].text.strip(),
                            "v4 Prefixes": cols[4].text.strip(),
                            "v6 Prefixes": cols[5].text.strip()
                        }
                        self.peer_data.append(row_info)

                # 2. 提取统计信息（<table>后紧跟的<p>标签）
                stats_p = h2.find_next('p')
                if stats_p:
                    stats = {}
                    # 提取位置
                    stats['location'] = location
                    # 提取IPv4全表数量
                    ipv4_match = re.search(r'IPv4 full tables: (\d+)', stats_p.text)
                    stats['ipv4_full_tables'] = ipv4_match.group(1) if ipv4_match else ""
                    # 提取IPv6全表数量
                    ipv6_match = re.search(r'IPv6 full tables: (\d+)', stats_p.text)
                    stats['ipv6_full_tables'] = ipv6_match.group(1) if ipv6_match else ""
                    # 提取总对等体数量
                    total_match = re.search(r'Total peerings: (\d+)', stats_p.text)
                    stats['total_peerings'] = total_match.group(1) if total_match else ""
                    # 提取数据时间
                    time_match = re.search(r'Data source time: (.+)', stats_p.text)
                    stats['data_source_time'] = time_match.group(1).strip() if time_match else ""
                    # 添加RRC名称
                    stats["rrc_name"] = rrc_name
                    self.stats_data.append(stats)

            print(f"对等体表格共提取 {len(self.peer_data)} 条数据")
            print(f"统计信息共提取 {len(self.stats_data)} 条数据")
            return True

        except Exception as e:
            print(f"爬取失败: {str(e)}", exc_info=True)
            return False

    def save_to_csv(self):
        """保存为两个CSV文件：对等体列表 + 统计信息"""
        # 保存对等体表格
        if self.peer_data:
            peer_csv = "/home/bgpdata/Domeye/backend/web/tests/result/rrc_peer_list.csv"
            fieldnames = ["rrc_name"] + self.peer_columns
            with open(peer_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.peer_data)
            print(f"对等体列表已保存至: {peer_csv}")

        # 保存统计信息
        if self.stats_data:
            stats_csv = "/home/bgpdata/Domeye/backend/web/tests/result/rrc_stats.csv"
            fieldnames = ["rrc_name", "location", "ipv4_full_tables", "ipv6_full_tables", "total_peerings", "data_source_time"]
            with open(stats_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.stats_data)
            print(f"统计信息已保存至: {stats_csv}")


class RouteViewsScraper:
    def __init__(self):
        self.url = "https://archive.routeviews.org/peers/peering-status.html"
        self.data = []
        
    def fetch_and_parse(self):
        """爬取并解析RouteViews对等体数据"""
        try:
            print("开始爬取RouteViews对等体数据...")
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            
            response = requests.get(self.url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # 获取页面文本内容
            soup = BeautifulSoup(response.text, "html.parser")
            text_content = soup.get_text()
            
            print("解析文本格式数据...")
            self.parse_text_data(text_content)
            
            print(f"成功解析 {len(self.data)} 条记录")
            return len(self.data) > 0
            
        except Exception as e:
            print(f"爬取失败: {str(e)}")
            return False
    
    def parse_text_data(self, text):
        """解析文本格式的对等体数据"""
        lines = text.split('\n')
        
        # 查找数据开始的位置（表头后面）
        data_start = False
        current_collector = ""
        
        for line in lines:
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 检测表头分隔线，数据从下一行开始
            if line.startswith('====') and '=' in line:
                data_start = True
                continue
            
            # 如果还没到数据区域，跳过
            if not data_start:
                continue
            
            # 检测收集器行（以.routeviews.org结尾）
            if '.routeviews.org' in line:
                # 解析收集器行
                parts = self.split_line_smart(line)
                if len(parts) >= 3:  # 至少要有收集器名称、AS号、IP地址
                    collector = parts[0]
                    as_number = parts[1]
                    peering_address = parts[2]
                    
                    # 解析第4列的复合信息（格式：前缀数量 | 国家代码 | 地区 | AS名称）
                    if len(parts) > 3:
                        combined_info = parts[3]
                        prefixes, cc, region, asname = self.parse_combined_info(combined_info)
                    else:
                        prefixes, cc, region, asname = "", "", "", ""
                    
                    record = {
                        "ROUTEVIEWS COLLECTOR": collector,
                        "AS NUMBER": f"AS{as_number}" if as_number.isdigit() else as_number,
                        "PEERING ADDRESS": peering_address,
                        "PREFIXES": prefixes,
                        "CC": cc,
                        "REGION": region,
                        "ASNAME": asname
                    }
                    
                    self.data.append(record)
                    current_collector = collector
                    
            # 检测继续行（可能是同一收集器的其他AS）
            elif current_collector and re.match(r'^\s+\d+', line):
                # 这是上一个收集器的额外AS条目
                parts = self.split_line_smart(line.strip())
                if len(parts) >= 2:
                    as_number = parts[0]
                    peering_address = parts[1]
                    
                    # 解析第3列的复合信息
                    if len(parts) > 2:
                        combined_info = parts[2]
                        prefixes, cc, region, asname = self.parse_combined_info(combined_info)
                    else:
                        prefixes, cc, region, asname = "", "", "", ""
                    
                    record = {
                        "ROUTEVIEWS COLLECTOR": current_collector,
                        "AS NUMBER": f"AS{as_number}" if as_number.isdigit() else as_number,
                        "PEERING ADDRESS": peering_address,
                        "PREFIXES": prefixes,
                        "CC": cc,
                        "REGION": region,
                        "ASNAME": asname
                    }
                    
                    self.data.append(record)
    
    def parse_combined_info(self, combined_text):
        """解析复合信息字段：前缀数量 | 国家代码 | 地区 | AS名称"""
        prefixes = ""
        cc = ""
        region = ""
        asname = ""
        
        if not combined_text:
            return prefixes, cc, region, asname
        
        # 按 | 分割
        parts = [part.strip() for part in combined_text.split('|')]
        
        if len(parts) >= 1:
            # 第一部分是前缀数量（纯数字）
            first_part = parts[0].strip()
            if first_part.isdigit():
                prefixes = first_part
        
        if len(parts) >= 2:
            # 第二部分是国家代码（2个字母）
            second_part = parts[1].strip()
            if len(second_part) == 2 and second_part.isalpha():
                cc = second_part.upper()
        
        if len(parts) >= 3:
            # 第三部分是地区（如ripencc, arin等）
            region = parts[2].strip()
        
        if len(parts) >= 4:
            # 第四部分是AS名称
            asname = parts[3].strip()
        
        return prefixes, cc, region, asname
    
    def split_line_smart(self, line):
        """智能分割行数据，处理多个空格分隔的字段"""
        # 先按多个空格分割
        parts = re.split(r'\s{2,}', line.strip())
        
        # 过滤空字符串
        parts = [part.strip() for part in parts if part.strip()]
        
        return parts
    
    def save_to_csv(self):
        """保存数据到CSV文件"""
        if not self.data:
            print("没有数据可保存")
            return
        
        csv_filename = "/home/bgpdata/Domeye/backend/web/tests/result/routeviews_peering_status.csv"
        
        fieldnames = [
            "ROUTEVIEWS COLLECTOR",
            "AS NUMBER",
            "PEERING ADDRESS", 
            "PREFIXES",
            "CC",
            "REGION",
            "ASNAME"
        ]
        
        try:
            with open(csv_filename, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.data)
            
            print(f"CSV文件已生成: {csv_filename}")
            print(f"共保存 {len(self.data)} 条记录")
            
            # 显示前几条记录作为预览
            print("\n前3条记录预览:")
            for i, record in enumerate(self.data[:3]):
                print(f"记录 {i+1}: {record}")
                
        except Exception as e:
            print(f"保存CSV失败: {e}")


import pandas as pd
if __name__ == "__main__":
    df1 = pd.read_csv("/home/bgpdata/Domeye/backend/web/tests/result/routeviews_peering_status.csv")
    df2 = pd.read_csv("/home/bgpdata/bgpcore/info/as_entity.csv", usecols=['asn', 'global_rank'])

    df1 = df1.rename(columns={"AS NUMBER": "asn"})
    df1["asn"] = df1["asn"].str.replace("AS", "").astype(str)

    df2["asn"] = df2["asn"].astype(str)

    df1 = df1.merge(df2, on="asn", how="left")
    df1 = df1.rename(columns={"global_rank": "as_rank"})
    df1["as_rank"] = df1["as_rank"].fillna(0).astype(int)
    df1.to_csv("/home/bgpdata/Domeye/backend/web/tests/result/routeviews.csv", index=False, encoding='utf-8-sig')

    # print("选择要执行的爬虫:")
    # print("1. RouteViews 对等体状态爬虫")
    # print("2. RIPE RIS 对等体列表爬虫")
    
    # choice = input("请输入选择 (1-4): ").strip()
    
    # if choice == "1":
    #     print("执行 RouteViews 爬虫...")
    #     scraper = RouteViewsScraper()
    #     if scraper.fetch_and_parse():
    #         scraper.save_to_csv()
    #     else:
    #         print("爬取失败")
            
    # elif choice == "2":
    #     print("执行 RIPE RIS 爬虫...")
    #     scraper = RIPERISScraper()
    #     if scraper.fetch_data():
    #         scraper.save_to_csv()
    #     else:
    #         print("爬取失败")
            