from datetime import datetime, timedelta

def get_target_sunday(add_weeks=3):
    # 获取今天日期
    today = datetime.now().date()
    print(f"今日日期：{today}")
    
    # 先加上指定周数的天数
    total_days = add_weeks * 7
    target_date = today + timedelta(days=total_days)
    
    # weekday() 0=周一,6=周日
    weekday = target_date.weekday()
    # 提前初始化 sunday 变量，彻底消除未定义警告
    sunday = None
    if weekday == 6:
        sunday = target_date
    else:
        days_to_sunday = 6 - weekday
        sunday = target_date + timedelta(days=days_to_sunday)
    
    print(f"增加{add_weeks}周后首个周日：{sunday}")
    return sunday

if __name__ == "__main__":
    res = get_target_sunday(3)
    # 外部调用示例
    print("返回的周日日期：", res)