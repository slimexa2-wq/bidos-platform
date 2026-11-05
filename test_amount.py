def num2cn(num):
    if num is None:
        return ""
    num = round(float(num), 2)
    if num == 0:
        return "零元整"

    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟", "万", "拾", "佰", "仟", "亿", "拾", "佰", "仟"]

    int_part = int(num)
    dec_part = int(round((num - int_part) * 100))

    res = ""
    if int_part > 0:
        int_str = str(int_part)
        n = len(int_str)
        zero_flag = False
        for i in range(n):
            d = int(int_str[i])
            if d != 0:
                if zero_flag:
                    res += "零"
                    zero_flag = False
                res += digits[d] + units[n - i - 1]
            else:
                zero_flag = True
                if n - i - 1 == 4:
                    res += "万"
                    zero_flag = False
                elif n - i - 1 == 8:
                    res += "亿"
                    zero_flag = False
        res += "元"

    if dec_part == 0:
        res += "整"
    else:
        jiao = dec_part // 10
        fen = dec_part % 10
        if jiao > 0:
            res += digits[jiao] + "角"
        elif int_part > 0 and fen > 0:
            res += "零"
        if fen > 0:
            res += digits[fen] + "分"

    return res.replace("亿万", "亿")

print(num2cn(12345.67))
print(num2cn(1000000))
print(num2cn(12000000))
print(num2cn(1000500))
