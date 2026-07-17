import time
import sys
import os
import re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.get_event import get_event, deal_event, get_total_page

from config.config import conn_11

start = time.time()
page_size = 10

page_num = 1

event_type = ""
level = ""
country = ""
attacker_as = ""
attacked_as = ""
attacker_org = ""
attacked_org = ""
attacker_country = ""
attacked_country = ""
event_info = ""
sort_mode = ""
# start_time = request.args.get('start_time')

start_time = "2025-06-28 00:00:00"
end_time = "2025-07-28 00:00:00" 


state = None
judge_reason = ""
judge_userid = ""
judge_username = ""
judge_time = ""
notify_userid = ""
notify_username = ""
notify_time = ""

# date = str(datetime.datetime.now())[0:7].replace('-', '')
# date_lm = str(datetime.datetime.now().date() - relativedelta(months=1)).replace('-', '')[0:6]
# event_table = 'event_table_' + date
# event_table_lm = 'event_table_' + date_lm

# TODO: These utility functions need to be imported correctly.
# Assuming they are available for now.
event_rows = get_event(conn=conn_11, page_num=page_num, page_size=page_size, 
                        level=level, event_type=event_type, country=country, 
                        attacker_as=attacker_as, attacked_as=attacked_as, 
                        attacker_org=attacker_org, attacked_org=attacked_org,
                        attacker_country=attacker_country, attacked_country=attacked_country,
                        event_info=event_info, start_time=start_time, end_time=end_time, sort_mode=sort_mode, state=state,
                        judge_reason=judge_reason, judge_userid=judge_userid, judge_username=judge_username,
                        judge_time=judge_time, notify_userid=notify_userid, notify_username=notify_username,
                        notify_time=notify_time)
event_items = deal_event(event_rows=event_rows)

end = time.time()
print(end - start)

total_page, record_count = get_total_page(conn=conn_11, page_size=page_size, 
                        level=level, event_type=event_type, country=country, 
                        attacker_as=attacker_as, attacked_as=attacked_as, 
                        attacker_org=attacker_org, attacked_org=attacked_org,
                        attacker_country=attacker_country, attacked_country=attacked_country,
                        event_info=event_info, start_time=start_time, end_time=end_time, state=state, 
                        judge_reason=judge_reason, judge_userid=judge_userid, judge_username=judge_username,
                        judge_time=judge_time, notify_userid=notify_userid, notify_username=notify_username,
                        notify_time=notify_time)
d = dict()
d['total_page'] = total_page
d['record_count'] = int(record_count)
d['data'] = event_items


print(d)