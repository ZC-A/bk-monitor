# -*- coding: utf-8 -*-
"""
Tencent is pleased to support the open source community by making 蓝鲸智云 - 监控平台 (BlueKing - Monitor) available.
Copyright (C) 2017-2021 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://opensource.org/licenses/MIT
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
"""

import abc
import base64
import json
import re
from copy import deepcopy
from typing import Dict, List, Tuple, Union

import six
from django.conf import settings
from rest_framework import serializers

from core.drf_resource import api
from core.drf_resource.contrib.api import APIResource
from core.errors.api import BKAPIError


class CMSIBaseResource(six.with_metaclass(abc.ABCMeta, APIResource)):
    base_url = "%s/api/c/compapi/v2/cmsi/" % settings.BK_COMPONENT_API_URL
    module_name = "cmsi"


class CheckCMSIResource(CMSIBaseResource):
    """
    校验是否发送成功
    1.result = True 返回结果：
        {
            "username_check": {"invalid": []},
            "message": _("发送成功")
        }
    2.result = False 并且 接口返回失败人员名单 返回结果：
        {
            "username_check": {"invalid": ["admin"]},
            "message": str(e)
        }
    3.result = False 并且 无法获取失败人员名单 返回结果：
        {
            "username_check": {"invalid": []]},
            "message": str(e)
        }
    """

    def perform_request(self, validated_request_data: Dict[str, Union[List[str], any]]):
        """
        发送请求
        """
        """
        - 从参数中判断区分出内部用户和外部用户，
        - 内部用户通过 reciver_username 发送
        - 外部用户通过 reciver发送
        """

        self.message_detail = {}

        receivers = self.get_receivers(validated_request_data)

        self.send_request(validated_request_data, receivers)

    def get_receivers(self, validated_request_data: Dict) -> List[str]:
        """
        获取接收用户列表
        """
        if validated_request_data.get("receiver__username"):
            # validated_request_data["receiver"] 转换为邮箱地址, 逗号连接
            # receivers 变量表示接收用户列表
            receivers: List[str] = self.get_receivers_from_receiver_username(validated_request_data)
        elif isinstance(validated_request_data["receiver"], list):
            receivers: List = validated_request_data["receiver"]
            validated_request_data["receiver"] = ",".join(receivers)
        else:
            receivers: List = validated_request_data["receiver"].split(",")

        return receivers

    def send_request(self, validated_request_data: Dict, receivers: List[str]):  # send_failed_users
        """
        发送请求
        """
        # 待返回数据格式， 假定默认都是成功发送
        response_data = {
            # invalid: 通知失败的用户名列表
            "username_check": {"invalid": []},
            "message": "发送成功",
        }

        try:
            super(CMSIBaseResource, self).perform_request(validated_request_data)

            response_data["message_detail"] = self.message_detail
            return response_data

        except BKAPIError as e:
            invalid = receivers
            if isinstance(e.data, dict):
                try:
                    # api 返回告诉哪些人失败， 但不一定有， 没有的时候， 默认所有通知人都失败
                    invalid = e.data["data"]["username_check"]["invalid"]
                except (KeyError, TypeError):
                    pass
                if not invalid:
                    # 如果在错误结果情况下，返回的invalid用户为空的时候，判断所有的用户为失败
                    invalid = receivers

            # 通知失败的人员列表
            response_data["username_check"]["invalid"] = invalid
            # 失败原因
            response_data["message"] = str(e)
            # 记录失败的具体原因 {username: reason}， 仅失败才有
            response_data["message_detail"] = self.message_detail

            return response_data

        except Exception as e:
            self.report_api_failure_metric(error_code=getattr(e, 'code', 0), exception_type=type(e).__name__)
            # 其他没有处理到的异常，默认发送失败

            response_data["username_check"]["invalid"] = receivers
            response_data["message"] = str(e)
            return response_data

    def get_receivers_from_receiver_username(self, validated_request_data) -> List[str]:
        receivers: List[str] = validated_request_data["receiver__username"].split(",")

        if not settings.BK_USERINFO_API_BASE_URL or not isinstance(self, (SendMail, SendSms)):
            return receivers

        # 获取用户信息
        param = {"usernames": validated_request_data["receiver__username"], "fields": "email,phone"}
        receivers_info = api.bk_login.get_user_sensitive_info(**param)["data"]

        # 转化格式  -> {username: { "email": email, "phone": phone }}
        # e.g. {"zhangsan": {"email": "zhangsan@qq.com", "phone": "+8612312312345"}}
        receivers_info = {
            receiver["username"]: {
                "email": receiver["email"],
                "phone": f"+{receiver['phone_country_code']}{receiver['phone']}",
            }
            for receiver in receivers_info
        }

        # 提前获取失败原因为 "用户不存在" 的用户, 并且不会他们进行发送
        not_exist_usernames = [username for username in receivers if username not in receivers_info.keys()]
        self.message_detail.update({username: "user not exists" for username in not_exist_usernames})

        # 对应不同的子类，转化对应的receivers返回出去
        if isinstance(self, SendMail):
            validated_request_data["receiver"] = ",".join([info["email"] for info in receivers_info.values()])
        # sms 暂不处理
        # if isinstance(self, SendSms):
        #     validated_request_data["receiver"] = ",".join([info["phone"] for info in receivers_info.values()])

        return list(set(receivers) - set(not_exist_usernames))


class GetMsgType(CMSIBaseResource):
    """
    查询通知类型
    """

    action = "get_msg_type"
    method = "GET"

    def render_response_data(self, validated_request_data, response_data):
        for msg_type in response_data:
            msg_type["channel"] = "user"
        if settings.WXWORK_BOT_WEBHOOK_URL:
            response_data.append(
                {
                    "icon": "iVBORw0KGgoAAAANSUhEUgAAANcAAADICAMAAABMI4TJAAACvlBMVEUAAAAA//8AgP8Aqv8AgL8AmcwAqtU"
                    "Aktsgn98"
                    "cjuMameYXi9EVldUUndgSktsRmd0Qj98PluEOnNUNmdkMktsXl9wWm94UmdYUk9gTl9kSktsSldwQlN4Ql9cPk9gPlt"
                    "oPmdsVldwUlN0UltgTmdkSmNsSlNsRl9wRlt4QldoQl9sUlNsUltwUmN0Tld0Tl9kTlNoSltsSmNsRl9wRld0RltkQl"
                    "NoQltsUldwTl9wTlN0SldsSldwRltwRmN0RltoRl9oRldsTlNsTltwTl9wTldoSl9oSldsSltsSl9sSldwRl9wRldoR"
                    "ltoRl9sRltsTl9sTltwSltoSl9sSldsSltsSldwSltwRl9oRltoRl9sRldsTltsTl9wTltwTl9oSldoSltsSldsSlts"
                    "RltoRldoRltsTltsTltsTldwTltoSl9oSltsSl9sSldsSltsSldwSltwRl9oRltsRltsTldsTltsTl9wSltwSltoSld"
                    "sSltsSl9sSltsSl9wSldwRltoRldsRltsTltsTltwSldwSltoSl9sSltsSltsSldsSl9wSltoRltsRltsTltsTltsSl"
                    "9wSltoSltsSltsSl9sSltwSldoRltsRl9sRltsTltsTltsSltwSldoSltsSltsSltsSltsSldsSltwSl9oSltsRltsR"
                    "ltsTltsSl9sSltwSltoSltsSltsSldsSltsSltsSltsSltoSldsSltsRl9sTltsSltsSltsSltoSl9sSltsSltsSlts"
                    "SltsSldsSltwSltsSltsRltsTltsSltsSltsSltwSltsSltsSltsSl9sSltsSltsSltwSltsSldsSltsTltsSltsSlt"
                    "sSltsSltsSltsSltsSltsSltsSltsSltsSltsSltsSltsTltsSltsSltsSltsSltsSltsSltsSltv///8eB7cZAAAA6"
                    "HRSTlMAAQIDBAUGBwgJCgsMDQ4PEBESFBUWFxkaGxwdHyAhIiMkJicoKissLjAxMjM0NTY3ODk7PD0+P0FCQ0ZISUpL"
                    "TE1PUFFSU1RVVldYWVpbXF1fYWJjZGVmZ2hpamtsbW5vcHFydXZ3eXp7fH1+f4CBgoOEhYaHiImKi4yNjo+QkZKTlJa"
                    "XmJmam5ydn6ChoqOlpqeoqqutrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t"
                    "/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+D+lCiQAAAAFiS0dE6VHTR5QAAAaKSURBVHja7d3pX1RVGAfwB"
                    "4ZJQFMEDSETKdMQNS1NM9QkNMVUErS0UjPJCrdsUZNccm3fNMklRbHcRQU0QUAKilwpQNQAWef5M3ohyjBzz8yZOfdy"
                    "77k9v7d8DpwvM3DP88y55wK0W7o/l7rrRFHh0bSVo+8Hs8Q/4Qi2pjF9lI8ZVH5zKtAhhbHys57MQ4V8311y1qwGVMz"
                    "Vx2RW+XyCrFQ9JbFrGbJT019a1gx0ldIQSVm9aly6cK+krp3oJmOlZI10x8JCq4yu79y6cJKErC617l0/Seia7J6FjQ"
                    "/I5/qQw4Xx8rl287iWyuf6jce1XT7XnzyubJO6cuRz/W5S1y4e12H5XEt5XGvlcz3L40qSz2W95p7VHCHhQup99659M"
                    "q7nw/916xonZQH2ujvWcV8pXZZTrlk1kZI2AsJKXbFsSSBrIstcuN4EeRN1iflqLQCZE3JQmVU+BuTOfaubFFgZYSB9"
                    "ok46qgomgBliee1ve1XJNAuYJB2X1d9bEib7gYkysGUVbIsHc2XAHddmMFtqERFtYaZz1SAiloM5XVfJRS5ykYtc5CI"
                    "XuchFLnLpkU5dxVKLiFjm3diwR/tHdFZd1GfJwYtNqHeafk1Qdx/34kY0SBaryUpCw6RZzS5CiXFcOFc9Vm8DsdTsZo"
                    "01kuuAeq6pRnKdVM81w0iuc+q5XjKS6wK5yEUucpGLXOQiF7nIRS5ymcrVIVQKV2gEf6/04VmbT1XhZSlcGYhYnb1+a"
                    "ld3qL4rWvYZy+NCRGzYG+tiv7rPmKP3hsvlQsTiqawW9+Asu+HSuRCzFE9RCNzQjHK7sP4t5zdjv4K2w2V0IaZ1cmCN"
                    "uIFmcGFOcBvW6Do0hwtz7P/lD3M+JUNWF/7Suif6wXI0j6v1fk2/46iCy1ZWXCE04ZtF5yvVcNliWr44D4VduSnRVgC"
                    "wRs0/5JXpzMInOgAA+A96r0jUhZfuHJPW45ao63yc3aV+wHaPVYdH2v0w38Qrgi5cBAAAq1HQtcm/7TVjYpVHqvp5Dg"
                    "ugoAxB160QAAipEXS94XSNj7ziAat6qNN469diLkwG5p2d3K7lSlVBJf+r9YxS9Zcp5ir2AcgRc2Ur1gcvcLsWKS7Bw"
                    "24JuXAAhKGQyzZEuTb4mZNV7K88/l0x10Lm0TqcrmOMmmcUp4u1GyOoWsiVDmvFXLMZ87KWc7Gae7Bq3DQh12XYJ+bq"
                    "x5oX31Usn1m7TxdyIRSLubqw5rWay5XOdA0RdFUKuWqY81rA5fqKOb6noKteyFXH3A64nMu1h+nqJui6KfY+ZJ7L+AW"
                    "Xi715JlrQlS/mimbNK5PL1RDMGj9eyHWDeWQQp4t1h38w56bZRJZrs5DrBKSIuc4wppXAeV3OZPyBWiuEXBuYJ0ryrg"
                    "+fVpyWbx7v+nCSpztvuVzjwXpdzHVO8Yb4ybwsvKR46nLQP0KuhiCAzwXrlGUK04q4zl9/nQxQeLm3itUpPwLA44Iun"
                    "OY0ra5nPamX9zt/yLNOsK6MBQA4Luhqmu/wtx9R6Fl7o9jh84Iu3wr2AYosAAAjRPsbuM1+T7vlZY/bbc1fPtQ63m9i"
                    "qWjfZsqdL+4Q7rPVruh193eddM6bPlvTseRhvQMAgmOWlAr32bJa/pWFV6nQF81LnTsx/oM9t/Xv9zbee1tPUaPfa5g"
                    "+tt0dLKtM5Dpgd0G1bDeNK6/NStqaZhJXiUPDxG+dKVy54U7X+Fdvy+/aobT5pm+O5K7bc5SLHsv0azK7dvZmNhUCZh"
                    "ZJ6rLtHe56g9Tg1HybdK6CJX04NrR1i5m95rONUrhStm7d+Hactyfz035RcpGLXOQiF7nIRS5ykYtc5PofunLVcyUay"
                    "XVaPdfzRnLtV88VbSTXevVcflUGciWq5+J7rmH7pDZIRVdglmFcM0HNBH50wxCqigRQOQEjZi/9eJP7nNbMVJe9ZZw/"
                    "6JV3vJnyxeQJbhI7MNQH9IxXLgkeB+6VK4pc5CIXuchFLn2z3jSukPgVuzPu5jCaxPXIDyofB15V9E2c/g9zG16tSW9"
                    "G72f8BpZpU5P8Eayv60Wtiq1V+ro+1cpVpu9jfvdpVh331NWVqZlroK6uU5q5hpCLXORqM5cjGULJMqorXOybDSUXuc"
                    "hFLnKRi1zkIpe5XLnZQrlAdQq5yNU+/Sh9XemauaJ1dW3RzBWuq0uzJyX+pStLs89TGMeOtl/GN2vCOttRZ5enhxDzZ"
                    "Wc30D2dX9l2pkTFFBxaOUh8Vv8BnYouHcnCGosAAAAASUVORK5CYII=",
                    "is_active": "true",
                    "label": "群机器人",
                    "type": "wxwork-bot",
                    "channel": "wxwork-bot",
                    "name": settings.WXWORK_BOT_NAME,
                }
            )
        if settings.BKCHAT_API_BASE_URL:
            response_data.append(
                {
                    "icon": "iVBORw0KGgoAAAANSUhEUgAAAMgAAADICAMAAACahl6sAAACglBMVEUAAAD///+A//+qqv+/v/+ZzP+q1f+Stv+fv/"
                    "+qxv+ZzP+iuf+qv/+dxP+kyP+qzP+fv/+qxv+hyf+mv/+ewv+ixf+myP+fyv+jwv+nxP+hxv+kyP+nwf+ixP+lxf+f"
                    "x/+iwf+lw/+gxf+jxv+lyP+hw/+jxP+mxv+ix/+kwv+gxP+ixf+kxv+hyP+jw/+hxv+jx/+lw/+ixP+kxf+lxv+iw/"
                    "+kxP+hxf+jxv+ixP+jxP+lxf+ixv+jw/uixfuhx/ujxPuixvukxPyixPyjxfykxvyiw/yjxPyhxfyjxvykxvyixPyj"
                    "xfykxfyixvyjxPykxPyixfyjxvykxvyjxPykxfyjxvykxPyixfykxvyixPykxfyjxv2jxv2ixP2jxf2kxf2ixv2kxf"
                    "2ixf2jxv2kxv2ixP2jxf2kxf2jxv2jxP2ixf2jxf2ixP2jxP2ixf2jxf2jxP2kxf2ixP2jxf2jxP2ixf2jxf2jxv2i"
                    "xP2jxv2kxP2jxf2jxf2kxv2jxP2jxf2ixf2jxf2jxv2jxf2kxf2jxP2kxf2jxf2jxf2jxf2ixf2kxf6jxf6jxf6kxv"
                    "6jxP6ixf6jxf6jxv6ixf6jxf6jxf6jxv6jxP6kxf6jxf6kxP6jxf6jxf6ixf6jxvyjxfyjxfyjxfyjxPyjxfykxfyjx"
                    "fyjxvyixfyjxfyjxfyjxv2jxf2jxf2jxf2jxP2jxf2jxf2kxf2jxv2jxf2ixf2jxf2jxf2jxf2jxf2jxf2jxv2jxf2i"
                    "xf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxP2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2jxf2"
                    "jxf2jxf3xeazDAAAA1XRSTlMAAQIDBAUGBwgJCgsMDQ4PEBITFBUWFxgZGhscHR4fICEiIyQlJicoKSorLC0uLzEyMz"
                    "Q1Njc4OTo8PT4/QEJERUdJSktMTU5PUFFSU1RVVldYWVpbXF5fYGJjZWZnaGlqa21ub3BxcnN0dXZ3eXp8gIKDh4iLj"
                    "I2Oj5OUlZaXmJmam5yen6GipKanqKqrrK2usLGys7S1tre4uru8vb6/wcLDxMXGx8jJysvMzc7Q0dLT1NXW19jZ2tzd"
                    "3uDi4+Tl5unr7O7v8PHy9PX29/j5+vv8/f6c8424AAAAAWJLR0QB/wIt3gAABNpJREFUeNrt3elfVFUcx/E7wMjikpC"
                    "iWWqpKWZZYSqZW6iEC1pJuJsVZtlUtlHaClJGmklFRbmhZgpBbkRSrkWIyDIz9/v/9KAnMDM3hnvP8jv2+zy+T96vOz"
                    "Pn3DMz51gWx3Ecx3EcZ2xpcwq3bCmcnWq2wre0sh0AgPbKJT5zHbNOoEfHc0x1rA+iV6GAmY4diOpNEx3FiNFa8xwzg"
                    "rEgHRONgxxCzCpMcyyI7UB4smGQCgcIXjXL4W9xgtSYBXnAyYFOsyB5jhCYNeta5wwZaRRkszNk7M0Cud8cRUbBBxec"
                    "IfapdxYONEAx+sWTYfRV++4lKaQVQzYdtRFfraVTyTKy3mtDf6pZ4afImFlto7+dX5VEjTHtW7iqqTCBEuOOPTbcdiK"
                    "bzgyxuA0esncNo+HIaYLHruYTYCRtC8F7uwbrdkz6GUJqflCvY2ErBNW5WiPDFwhDXB9pGx5T9kFoVZqeuQb9AMHV3K"
                    "LDkX4cwjt9m3rHsLOQ0LnhyifsdZBSY6ZaR+phSKpO6dDor4K0DgxQOH5UQGKl6iABSG2jKsfDQbmQrpmKPngvQXIX0"
                    "lU4Eqohvc9VQF6GgpbLd4y7oQJyRf6sqwpKKpHtWKbGgaDkdci0PxRBcETuT1fegLIKpD6DtKmDNMi8JdugsFx5joF/"
                    "qoT8KA+yFUqbJcuRfEUtpEoWpECtA6ERkiCViiHYLOmzt1M1pFYO5Gko7x4pkGPqIa/LcIyy1UNOy4AUqnfAlrHwWKo"
                    "BImXm+JsOyA7xjpE6HDgjHrJSC8QW/811iRYI5gqHfKUHIn75tFEP5G3RjsQuPZCvRUPG63GgSTQkVxOkO1EwpEgTBK"
                    "LXTp/RBbndrC+pnMsSDHlNF2SaYMh2XRDRQ3uZLshjgiEf64IsZghDGMIQhjCEIQxhCEMYwhCGMIQhDGEIQxjCEIYwh"
                    "CEMYQhDGMIQhvyPIEd1QT4U65iry4HuO4VCDmuDoEykY74+B7rvEgip0QhBufm/A/y3kLjNjn/SCsEnohyL9DoQmiQI"
                    "clIzBJ+KceTrdiA8RYTDV6sdgs9EQJbqdyB8n4Ab0kAAImJbjuUUHLA975yQ8AsJCPZ5hTxBwwHb4w+ZE88RgeBLb5C"
                    "nQKaHPN2QX+lAPP1NfxUINd3DDWmiBPnOPWQNSOV6Mwt/My3I924h60Cs2S5vyO/UIAfdQdaCXO7+3rOfHuQLV/P3Ln"
                    "qQBjeQdHoOd5tZ+AnekZ2u3iO19CB5N8cwgnp3m6Al1xFz3HA7kx97npSjxf1eboNLrpJhtO8c4+WBJGncI/NIlJVsc"
                    "RzHcTEHnd6flw7HGg/tfdUMghB/xEF1z8a6KKO+90VvUbwlz0csO78UfcnEiJXL4BiSr63IA0/33hrxzLnxWsQVe2i+"
                    "S16JnB/9HRjaY9lycX3UN2rZNCHJ0Sus13cXTRlgWUkTlr1/OXoiWE71g+vR2BPXDoet5lszqUKs/h3n8RzdsWTQmX4"
                    "4Kn10Idbd8Z801jiE9PieH+/ZJH9NID5TWRTf9sYt2Rb1cjvicDRnWfSb0/dm5t9kGDEPzuzjINSOF3yWIT1++T8c1e"
                    "MNejhJK3Z6fR3Ls8wqtSjG6c3Xy6dbBjZqw96LPZ496t9dkGIZ2/CcJzcFAlvX592banEcx3Ecx3GG9g/bcdcQHRblg"
                    "QAAAABJRU5ErkJggg==",
                    "is_active": "true",
                    "label": "蓝鲸信息流",
                    "type": "bkchat",
                    "channel": "bkchat",
                    "name": "蓝鲸信息流",
                }
            )

        return response_data


class SendMsg(CheckCMSIResource):
    """
    通用发送消息
    """

    action = "send_msg"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        sender = serializers.CharField(required=False)
        receiver__username = serializers.CharField()
        title = serializers.CharField()
        content = serializers.CharField()
        msg_type = serializers.CharField()
        is_content_base64 = serializers.BooleanField(default=False)

        def validate(self, attrs):
            if attrs["is_content_base64"]:
                attrs["content"] = base64.b64encode(attrs["content"].encode("utf-8")).decode("utf-8")
            if attrs["msg_type"] == "wecom_robot":
                attrs["wecom_robot"] = json.loads(attrs["content"])
            return attrs


class SendWeixin(CheckCMSIResource):
    """
    发送微信消息
    """

    action = "send_weixin"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        receiver__username = serializers.CharField()
        heading = serializers.CharField()
        message = serializers.CharField()
        date = serializers.CharField(required=False)
        remark = serializers.CharField(required=False)
        is_message_base64 = serializers.BooleanField(default=False)

        def validate(self, attrs):
            if attrs["is_message_base64"]:
                attrs["message"] = base64.b64encode(attrs["message"].encode("utf-8")).decode("utf-8")

            params = {
                "receiver__username": attrs["receiver__username"],
                "data": {
                    "heading": attrs["heading"],
                    "message": attrs["message"],
                    "is_message_base64": attrs["is_message_base64"],
                },
            }

            if "date" in attrs:
                params["data"]["date"] = attrs["date"]
            if "remark" in attrs:
                params["data"]["remark"] = attrs["remark"]

            return params


class SendMail(CheckCMSIResource):
    """
    发送邮件消息
    """

    action = "send_mail"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        class Attachment(serializers.Serializer):
            filename = serializers.CharField()
            content = serializers.CharField()
            type = serializers.CharField(required=False)
            disposition = serializers.CharField(required=False)
            content_id = serializers.CharField(required=False)

        receiver = serializers.CharField(required=False)
        receiver__username = serializers.CharField(required=False)
        sender = serializers.CharField(required=False)
        cc = serializers.CharField(required=False)
        cc__username = serializers.CharField(required=False)
        title = serializers.CharField()
        content = serializers.CharField()
        body_format = serializers.CharField(default="Html")
        is_content_base64 = serializers.BooleanField(default=False)
        attachments = Attachment(many=True, required=False)
        email_type = serializers.CharField(default="SEND_TO_INTERNET")

        def validate(self, attrs):
            if attrs["is_content_base64"]:
                attrs["content"] = base64.b64encode(attrs["content"].encode("utf-8")).decode("utf-8")

            return attrs

    def perform_request(self, validated_request_data: Dict[str, Union[List[str], any]]):
        """
        发送请求

        针对内部用户和外部用户做一个额外判断处理
        """
        self.message_detail = {}

        # 如果没有 receiver__username 说明是直接用 receiver 邮箱发送的
        # 交由父类直接处理
        if not validated_request_data.get("receiver__username"):
            super().perform_request(validated_request_data)

        # 判断是否存在有环境变量 BK_USERINFO_API_BASE_URL 如果存在则可能需要区分内外部用户
        if not settings.BK_USERINFO_API_BASE_URL:
            super().perform_request(validated_request_data)

        # 区分内外部用户
        internal_users, external_users = self.classif_users(validated_request_data.get("receiver__username"))

        # 内部用户 针对内部用户直接通过 receiver__username 发送
        if internal_users:
            request_data = deepcopy(validated_request_data)
            request_data["receiver__username"] = ",".join(internal_users)
            # 确保没有 receiver
            if request_data.get("receiver"):
                del request_data["receiver"]

            self.send_request(request_data, request_data["receiver__username"])

        # 外部用户 针对外部用户通过 需要转化成 receiver 邮箱的方式直接发送
        if external_users:  # <- 以@结尾,还需要转化成邮箱
            # receivers -> 邮箱
            receivers = self.get_receivers_with_external_users(external_users)

            request_data = deepcopy(validated_request_data)
            request_data["receiver"] = ",".join(receivers)  # receivers <- 接收邮箱
            # 确保没有 receiver__username
            if request_data.get("receiver__username"):
                del request_data["receiver__username"]

            self.send_request(request_data, external_users)  # receivers <- 用户名

    def get_receivers_with_external_users(self, external_users: List[str]) -> List[str]:
        """
        获取接收者

        调用查询用户信息的接口，返回的用户可能
        receivers: List[str]  # 邮箱不为空用户的邮箱
        """
        # 获取外部人员的信息
        receivers_info = self.get_receivers_info_from_receivers_username(external_users)

        # 提前获取失败原因为 "用户不存在" 的用户, 并且不会对他们进行发送
        not_exist_usernames = [username for username in external_users if username not in receivers_info.keys()]
        self.rich_message_detail_with_usernames(not_exist_usernames, "user not exists")

        # 提前获取失败原因为 "邮箱不存在" 的用户，并且不会对他们进行发送
        exist_usernames = list(set(external_users) - set(not_exist_usernames))
        not_email_usernames = [username for username in exist_usernames if receivers_info[username]["mail"] == ""]
        self.rich_message_detail_with_usernames(not_email_usernames, "user email not exists")

        # 获取最终的 receivers
        exist_usernames = list(set(exist_usernames) - set(not_email_usernames))

        return [receivers_info[username]["mail"] for username in exist_usernames]  # todo 有疑问？指的是谁  应该是 email

    def rich_message_detail_with_usernames(self, usernames: List[str], message_detail):
        """
        丰富异常用户的消息详情
        """
        self.message_detail.update({username: message_detail for username in usernames})

    def classif_users(self, usernames: List[str]) -> Tuple[List[str], List[str]]:
        """区分内外部用户

        return:
            internal_users: List[str]  # 内部用户
            external_users:List[str]  # 外部用户
        """
        internal_users: List[str] = []
        external_users: List[str] = []

        for username in usernames:
            # 通过是否以 "@tai" 结尾判断是否是内外部用户
            pattern_rules = [r"@tai$"]
            if self.is_external_user(username, pattern_rules):
                external_users.append(username)
            else:
                internal_users.append(username)
        return internal_users, external_users

    def is_external_user(self, username: str, pattern_rules: List[str]) -> bool:
        """
        使用正则匹配用户名判断是否是外部用户

        username: str -> 用户名
        pattern_rules -> 匹配的规则
        """
        for pattern in pattern_rules:
            match = re.search(pattern, username)
            if match:
                return True
        return False

    def get_receivers_info_from_receivers_username(self, receivers_username: List[str]) -> Dict[str, Dict[str, str]]:
        """
        通过用户名获取用户信息(邮箱和电话号)

        param
        ```python
        receivers_username  # 用户名列表
        ```
        return
        ```python
        {
            username: {
                "email": email | "",
                "phone": f"[+]{phone_country_code}{phone}" | "",
            }
        }
        ```
        """
        fields = "email,phone"
        param = {"usernames": receivers_username, "fields": fields}

        receivers_info = api.bk_login.get_user_sensitive_info(**param)["data"]

        # 转化格式  -> {username: { "email": email, "phone": phone }}
        # e.g. {"zhangsan": {"email": "zhangsan@qq.com", "phone": "+8612312312345"}}
        receivers_info = {
            receiver["username"]: {
                "email": receiver["email"],
                "phone": f"+{receiver['phone_country_code']}{receiver['phone']}" if receiver['phone'] else "",
            }
            for receiver in receivers_info
        }
        return receivers_info


class SendSms(CheckCMSIResource):
    """
    发送短信消息
    """

    action = "send_sms"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        receiver = serializers.CharField(required=False)
        receiver__username = serializers.CharField(required=False)
        content = serializers.CharField()
        is_content_base64 = serializers.BooleanField(default=False)

        def validate(self, attrs):
            if attrs["is_content_base64"]:
                attrs["content"] = base64.b64encode(attrs["content"].encode("utf-8")).decode("utf-8")

            return attrs


class SendVoice(CMSIBaseResource):
    """
    发送语言消息
    """

    action = "send_voice_msg"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        class UserInfo(serializers.Serializer):
            username = serializers.CharField()
            mobile_phone = serializers.CharField(required=False)

        user_list_information = UserInfo(required=False, many=True)
        receiver__username = serializers.CharField(required=False)
        auto_read_message = serializers.CharField()


class SendWecomRobot(CheckCMSIResource):
    """
    发送机器人消息
    """

    action = "send_wecom_robot"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        receiver = serializers.ListField(required=False, child=serializers.CharField())
        group_receiver = serializers.ListField(required=False, child=serializers.CharField())
        sender = serializers.CharField(required=False)
        type = serializers.CharField(required=False, default="text")
        content = serializers.JSONField(required=True)

        def validate(self, attrs):
            attrs[attrs["type"]] = {"content": attrs["content"]}
            return attrs


class SendWecomAPP(CheckCMSIResource):
    """
    发送企业号消息
    """

    action = "send_wecom_app"
    method = "POST"

    class RequestSerializer(serializers.Serializer):
        receiver = serializers.ListField(required=False, child=serializers.CharField())
        tag_receiver = serializers.ListField(required=False, child=serializers.CharField())
        sender = serializers.CharField(required=False)
        type = serializers.CharField(required=False, default="text")
        content = serializers.JSONField(required=True)

        def validate(self, attrs):
            attrs[attrs["type"]] = {"content": attrs["content"]}
            return attrs
