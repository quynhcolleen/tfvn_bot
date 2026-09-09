import asyncio
from dataclasses import dataclass

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from cogs._beta_function import beta_access_denial, is_beta_function

HELP_MENU_TIMEOUT_SECONDS = 180
HELP_SELECT_CUSTOM_ID = "tfvn:help:topic"
HELP_SELECT_PLACEHOLDER = "Chọn một chủ đề trợ giúp…"


@dataclass(frozen=True)
class HelpUsage:
    command_name: str
    text: str


@dataclass(frozen=True)
class HelpEntry:
    usages: tuple[HelpUsage, ...]
    description: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpSection:
    name: str
    entries: tuple[HelpEntry, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class HelpTopic:
    key: str
    label: str
    emoji: str
    option_description: str
    title: str
    description: str
    color: int
    sections: tuple[HelpSection, ...]
    always_available: bool = False


def _entry(
    command_name: str,
    description: str,
    usage: str | None = None,
    *,
    aliases: tuple[str, ...] = (),
) -> HelpEntry:
    return HelpEntry(
        usages=(HelpUsage(command_name, usage or command_name),),
        description=description,
        aliases=aliases,
    )


def _grouped_entry(
    usages: tuple[tuple[str, str], ...],
    description: str,
    *,
    aliases: tuple[str, ...] = (),
) -> HelpEntry:
    return HelpEntry(
        usages=tuple(HelpUsage(command_name, text) for command_name, text in usages),
        description=description,
        aliases=aliases,
    )


HELP_TOPICS = (
    HelpTopic(
        key="overview",
        label="Tổng quan",
        emoji="📖",
        option_description="Bắt đầu, lệnh chung và cách dùng menu",
        title="Hướng dẫn sử dụng TFVN bot",
        description=(
            "Danh mục đầy đủ các lệnh và tính năng của bot. Chọn một chủ đề "
            "bên dưới; khả năng sử dụng thực tế phụ thuộc cog, cấu hình và quyền Discord."
        ),
        color=0xFFC0CB,
        sections=(
            HelpSection(
                name="Bắt đầu",
                entries=(
                    _entry("help", "Mở menu này hoặc đi thẳng tới một chủ đề.", "help [topic]"),
                    _entry("hello", "Chào bot."),
                    _entry("invite", "Lấy liên kết mời bot."),
                    _entry("verify", "Đi tới kênh xác minh đã cấu hình."),
                    _entry(
                        "role_exam",
                        (
                            "Min mót có quyền Manage Roles mời `@user` làm bài riêng "
                            "20 câu từ JSON; đủ phần trăm cấu hình sẽ nhận role an toàn. "
                            "Trượt hoặc hết hạn cần min mót mở lại."
                        ),
                        "role_exam @user",
                    ),
                    _entry(
                        "self_unverified",
                        (
                            "Tự gỡ role xác minh sau Yes/No; hết hạn phải gọi lại; "
                            "muốn vào lại hỏi min mót."
                        ),
                    ),
                    _entry("ping", "Kiểm tra bot có đang phản hồi hay không."),
                ),
            ),
            HelpSection(
                name="Lối tắt",
                entries=(
                    _entry(
                        "mod",
                        "Mở trang quản trị; từng lệnh tự kiểm tra quyền cần thiết.",
                    ),
                    _entry(
                        "nsfw",
                        "Mở trang lệnh người lớn; chỉ dùng trong kênh NSFW.",
                    ),
                ),
                note=(
                    "Bạn cũng có thể dùng tên chủ đề sau `help`, ví dụ "
                    "`help games` hoặc `help community`."
                ),
            ),
            HelpSection(
                name="Cách đọc cú pháp",
                note=(
                    "`<...>` là bắt buộc · `[...]` là tùy chọn · `@user`/`@role` "
                    "là mention. Lệnh Beta chỉ hiện riêng cho thành viên có Beta role."
                ),
            ),
        ),
    ),
    HelpTopic(
        key="community",
        label="Cộng đồng",
        emoji="🫂",
        option_description="AFK, giờ ngủ, nhắc việc, sinh nhật và sự kiện",
        title="Cộng đồng & sự kiện",
        description="Các tiện ích giúp thành viên theo dõi và tham gia hoạt động server.",
        color=0x5865F2,
        sections=(
            HelpSection(
                name="AFK",
                entries=(
                    _entry("afk", "Xem hướng dẫn đặt AFK."),
                    _entry(
                        "afk dynamic",
                        "AFK tới tin nhắn tiếp theo; lý do là tùy chọn.",
                        "afk dynamic [lý do]",
                    ),
                    _entry(
                        "afk time",
                        "Bot hỏi thời lượng `d/h/m/s` và lý do.",
                    ),
                    _entry("afk clear", "Hủy một phiên AFK có thời hạn sớm."),
                    _entry(
                        "afk check",
                        "Xem rồi đánh dấu đã đọc các lượt nhắc khi AFK; dùng trong server.",
                    ),
                ),
            ),
            HelpSection(
                name="Nhắc việc & sinh nhật",
                entries=(
                    _entry("jobremind", "Xem hướng dẫn nhắc việc qua DM."),
                    _entry(
                        "jobremind add",
                        "Bot hỏi độ trễ `d/h/m/s` và tên việc, sau đó nhắc qua DM.",
                    ),
                    _entry(
                        "birthday",
                        "Mở biểu mẫu chọn tháng và ngày sinh.",
                    ),
                    _entry(
                        "birthday set",
                        "Lưu ngày sinh bằng cú pháp nhanh.",
                        "birthday set <ngày> <tháng>",
                    ),
                ),
            ),
            HelpSection(
                name="Giờ ngủ (UTC+7, quản trị)",
                entries=(
                    _entry(
                        "bedtime",
                        "Mở bảng Discord để thêm, xóa và xem lịch đi ngủ — Administrator, chỉ server.",
                    ),
                    _entry(
                        "bedtime add",
                        (
                            "Thêm hoặc cập nhật lịch hằng ngày và kênh thông báo "
                            "— Administrator."
                        ),
                        (
                            "bedtime add @member <bedtime_HH:MM> "
                            "<wake_HH:MM> #channel"
                        ),
                    ),
                    _entry(
                        "bedtime remove",
                        "Xóa lịch của member ngay lập tức — Administrator.",
                        "bedtime remove <@member|user_id>",
                    ),
                    _entry(
                        "bedtime list",
                        (
                            "Liệt kê có phân trang các lịch của server mà không ping "
                            "member — Administrator."
                        ),
                    ),
                ),
                note=(
                    "Bảng dùng user select, channel select và modal nhập giờ; "
                    "lệnh chữ vẫn dùng được. Bot tag một lần trong kênh đã chọn khi "
                    "tới giờ ngủ. Trong khoảng giờ ngủ (gồm) đến giờ dậy (không gồm), "
                    "mỗi tin nhắn của member ở server đều nhận reply nhắc ngủ có tag; "
                    "không có cooldown."
                ),
            ),
            HelpSection(
                name="Hoạt động cộng đồng",
                entries=(
                    _entry(
                        "random_femboy",
                        "Xem ảnh ngẫu nhiên và metadata từ bộ sưu tập femboy.",
                    ),
                    _entry(
                        "vote",
                        "Tạo vote; bot hỏi thời lượng và các lựa chọn khi cần.",
                        "vote [yesno|multiple|multiplechoice] [câu hỏi]",
                    ),
                    _grouped_entry(
                        (
                            ("giveaway", "giveaway"),
                            (
                                "giveaway",
                                "giveaway <thời lượng> [số người thắng] <phần thưởng>",
                            ),
                        ),
                        (
                            "Không đối số mở hướng dẫn; tạo giveaway 10 giây–30 ngày, "
                            "1–20 người thắng "
                            "(Admin/Manage Server/Manage Messages)."
                        ),
                        aliases=("ga",),
                    ),
                    _entry(
                        "giveaway list",
                        "Xem tối đa 25 giveaway đang hoạt động.",
                        aliases=("giveaway ls", "giveaway active"),
                    ),
                    _entry(
                        "giveaway entries",
                        "Xem người tham gia bằng ID hoặc reply tin giveaway.",
                        "giveaway entries [message_id]",
                        aliases=(
                            "giveaway entrants",
                            "giveaway joined",
                            "giveaway who",
                        ),
                    ),
                    _entry(
                        "giveaway end",
                        (
                            "Host hoặc Admin/Manage Server/Manage Messages kết thúc sớm "
                            "bằng ID hay reply."
                        ),
                        "giveaway end [message_id]",
                    ),
                    _entry(
                        "giveaway reroll",
                        (
                            "Host hoặc Admin/Manage Server/Manage Messages chọn lại "
                            "1–20 người thắng bằng ID hay reply."
                        ),
                        "giveaway reroll [message_id] [số người thắng]",
                        aliases=("giveaway rr",),
                    ),
                ),
            ),
            HelpSection(
                name="Cảnh cáo cá nhân",
                entries=(
                    _entry(
                        "check_warn",
                        "Xem tối đa 10 cảnh cáo gần nhất; mặc định là chính bạn.",
                        "check_warn [@user]",
                    ),
                ),
            ),
        ),
    ),
    HelpTopic(
        key="economy",
        label="Trap Coin & Shop",
        emoji="🪙",
        option_description="Số dư, giao dịch, cửa hàng và vật phẩm",
        title="Trap Coin & cửa hàng",
        description="Kiếm, kiểm tra và sử dụng Trap Coin trong server.",
        color=0xF1C40F,
        sections=(
            HelpSection(
                name="Tài khoản",
                entries=(
                    _entry("daily", "Nhận 10 Trap Coin một lần mỗi ngày UTC."),
                    _entry(
                        "user_balance",
                        "Xem số dư Trap Coin hiện tại.",
                        aliases=("balance",),
                    ),
                    _entry(
                        "user_transactions",
                        "Xem 10 giao dịch gần nhất.",
                        aliases=("transactions",),
                    ),
                ),
            ),
            HelpSection(
                name="Cửa hàng",
                entries=(
                    _entry("shop", "Xem vật phẩm đang bán.", aliases=("store",)),
                    _entry(
                        "shop buy",
                        "Mua một vật phẩm; tối đa 2 lần mỗi 5 giây/người.",
                        "shop buy <item_id>",
                    ),
                    _entry(
                        "shop inventory",
                        "Xem kho vật phẩm; mặc định là chính bạn.",
                        "shop inventory [@user]",
                        aliases=("shop inv",),
                    ),
                    _entry("shop use", "Dùng badge hoặc role đã mua.", "shop use <item_id>"),
                    _entry("shop unequip", "Gỡ badge đang trang bị."),
                ),
                note="Các lệnh quản lý số dư và danh mục shop nằm trong chủ đề Quản trị.",
            ),
        ),
    ),
    HelpTopic(
        key="cultivation",
        label="Tiên Lộ",
        emoji="☯️",
        option_description="Bế quan, đột phá, trang bị và thí luyện",
        title="Tiên Lộ — Tu Tiên AFK",
        description=(
            "Tu luyện khi offline, xây dựng nhân vật và chinh phục PvE. Dùng "
            "`tutien` (alias `cultivate`) để mở bảng điều khiển cá nhân. "
            "Hệ thống không có PvP hoặc giao dịch trực tiếp giữa người chơi."
        ),
        color=0x8E44AD,
        sections=(
            HelpSection(
                name="Bắt đầu & Bế Quan",
                entries=(
                    _entry(
                        "tutien",
                        "Mở bảng điều khiển Tiên Lộ chỉ người gọi được sử dụng.",
                        aliases=("cultivate",),
                    ),
                    _entry("tutien batdau", "Khởi tạo hồ sơ và bắt đầu Bế Quan."),
                    _entry(
                        "tutien thucong",
                        "Nhận thưởng AFK nếu đã tích lũy ít nhất 10 phút rồi tiếp tục Bế Quan.",
                    ),
                    _entry(
                        "tutien huong",
                        "Chọn Cân Bằng, Tĩnh Tu hoặc Khai Khoáng.",
                        "tutien huong <canbang|tinhtu|khaikhoang>",
                    ),
                    _entry(
                        "tutien dongphu",
                        "Xem cấp, sản lượng, sức chứa và giá nâng Động Phủ.",
                    ),
                    _entry(
                        "tutien dongphu nangcap",
                        "Mua cấp Động Phủ kế tiếp nếu đủ Linh Thạch.",
                    ),
                ),
                note=(
                    "Bế Quan và Bí Cảnh không thể chạy cùng lúc. Phần thưởng được tính "
                    "từ timestamp nên vẫn tích lũy khi bot ngừng hoạt động."
                ),
            ),
            HelpSection(
                name="Cảnh giới, phái & thiên phú",
                entries=(
                    _entry(
                        "tutien dotpha",
                        "Thử đột phá khi đủ Tu Vi, Linh Thạch và tầng tháp yêu cầu.",
                    ),
                    _entry(
                        "tutien phai",
                        "Xem phái hoặc chọn Kiếm Tu, Thể Tu, Đan Tu khi đạt Luyện Khí 1.",
                        "tutien phai [kiem|the|dan]",
                    ),
                    _entry(
                        "tutien phai reset",
                        "Đặt lại phái và hoàn toàn bộ điểm với phí cùng cooldown 7 ngày.",
                    ),
                    _entry(
                        "tutien thienphu",
                        "Xem talent ID, hiệu ứng, cấp hiện tại và điểm còn lại.",
                    ),
                    _entry(
                        "tutien thienphu tang",
                        "Cộng một hoặc nhiều điểm vào talent thuộc phái đã chọn.",
                        "tutien thienphu tang <talent_id> [points]",
                    ),
                ),
                note=(
                    "Tiểu cảnh giới luôn đột phá thành công. Đại cảnh giới dùng soft pity: "
                    "70%, +10 điểm phần trăm mỗi lần thất bại và lần thứ tư chắc chắn thành công. "
                    "Thất bại giữ Tu Vi/trang bị, chờ 1 giờ và mất phí Linh Thạch cơ bản 25%; "
                    "Thiên Phú Hộ Mạch có thể giảm phí đến 10%."
                ),
            ),
            HelpSection(
                name="Hồ sơ & riêng tư",
                entries=(
                    _entry(
                        "tutien profile",
                        "Xem hồ sơ toàn bot của bạn hoặc một member công khai.",
                        "tutien profile [@member]",
                    ),
                    _entry(
                        "tutien top",
                        "Top hồ sơ công khai trong số member nhìn thấy ở server hiện tại.",
                    ),
                    _entry(
                        "tutien riengtu",
                        "Xem hoặc đặt hồ sơ công khai/riêng tư.",
                        "tutien riengtu [public|private]",
                    ),
                ),
            ),
            HelpSection(
                name="Chợ, kho & trang bị",
                entries=(
                    _entry(
                        "tutien choden",
                        "Xem vật phẩm cơ bản và bốn ưu đãi luân phiên theo ngày ICT.",
                    ),
                    _entry(
                        "tutien mua",
                        "Mua vật phẩm từ Chợ Đen.",
                        "tutien mua <item_id>",
                    ),
                    _entry("tutien kho", "Xem nguyên liệu và trang bị đang sở hữu."),
                    _entry(
                        "tutien trangbi",
                        "Trang bị vật phẩm thuộc một trong bốn ô cố định.",
                        "tutien trangbi <item_id>",
                    ),
                    _entry(
                        "tutien phanra",
                        "Phân rã trang bị thành mảnh chế tạo.",
                        "tutien phanra <item_id>",
                    ),
                    _entry(
                        "tutien luyen",
                        "Xem công thức hoặc chế tạo vật phẩm theo công thức bảo đảm.",
                        "tutien luyen [recipe_id]",
                    ),
                ),
                note="Chợ không có reroll trả phí; trang bị không có độ bền hoặc chỉ số ngẫu nhiên.",
            ),
            HelpSection(
                name="Tháp Thí Luyện & Bí Cảnh",
                entries=(
                    _entry(
                        "tutien thiluyen",
                        "Đánh tầng kế tiếp trong tháp 30 tầng; mỗi tầng chỉ nhận thưởng một lần.",
                        "tutien thiluyen [tang]",
                    ),
                    _entry("tutien bicanh", "Xem hướng dẫn Bí Cảnh."),
                    _entry(
                        "tutien bicanh start",
                        "Bắt đầu chuyến đi 2/4/8 giờ với một hướng săn thưởng.",
                        "tutien bicanh start <linhduoc|cokhoang|yeuthuson> <2|4|8>",
                    ),
                    _entry(
                        "tutien bicanh claim",
                        "Nhận thưởng chuyến đi đã hoàn tất.",
                    ),
                    _entry(
                        "tutien bicanh cancel",
                        "Hủy chuyến đi đang chạy mà không nhận thưởng.",
                    ),
                ),
                note=(
                    "Khi Bí Cảnh kết thúc, nhân vật tự trở lại hướng Bế Quan trước đó. "
                    "Boss có pity trang bị hiển thị; lần đủ điều kiện thứ 10 chắc chắn có đồ."
                ),
            ),
            HelpSection(
                name="Đổi Trap Coin",
                entries=(
                    _entry("tutien doido", "Xem tỷ giá và hạn mức đổi trong tuần."),
                    _entry(
                        "tutien doido mua",
                        "Đổi tối đa 50 TC/tuần; 1 TC nhận 10 Linh Thạch.",
                        "tutien doido mua <số TC>",
                    ),
                    _entry(
                        "tutien doido ban",
                        "Đổi Linh Thạch để nhận tối đa 20 TC/tuần; 20 Linh Thạch đổi 1 TC.",
                        "tutien doido ban <so_linh_thach>",
                    ),
                ),
                note="Hạn mức đặt lại lúc 00:00 thứ Hai theo múi giờ Asia/Ho_Chi_Minh.",
            ),
        ),
    ),
    HelpTopic(
        key="games",
        label="Trò chơi",
        emoji="🎮",
        option_description="Casino, Cá sấu nha sĩ và game chữ",
        title="Trò chơi",
        description="Game dùng lệnh và game nhận câu trả lời trực tiếp trong kênh cấu hình.",
        color=0x2ECC71,
        sections=(
            HelpSection(
                name="Casino",
                entries=(
                    _entry(
                        "blackjack",
                        (
                            "Đánh Blackjack với nhà cái; cược 5–1.000.000 TC, "
                            "xì dách trả 3:2 (lẻ làm tròn xuống) và hòa hoàn tiền."
                        ),
                        "blackjack [số TC]",
                    ),
                    _entry(
                        "poker",
                        (
                            "Đấu Poker 5 lá một lượt đổi bài với nhà cái; "
                            "cược 5–1.000.000 TC."
                        ),
                        "poker [số TC]",
                    ),
                    _entry("slot", "Quay máy slot; mỗi lượt tốn 5 Trap Coin."),
                    _entry("flip_coin", "Đặt cược mặt đồng xu.", "flip_coin <head|tail> <số TC>"),
                    _entry(
                        "sicbo_start",
                        (
                            "Bắt đầu vòng chọn Tài/Xỉu/Bộ ba bằng reaction; "
                            "hiện không đặt cược hoặc trả Trap Coin."
                        ),
                    ),
                ),
            ),
            HelpSection(
                name="Cá sấu nha sĩ",
                entries=(
                    _entry(
                        "crocodile",
                        "Xem tối đa 10 ván đang chờ hoặc đang chơi của bạn trong server.",
                    ),
                    _entry(
                        "crocodile challenge",
                        (
                            "Mời 1–4 người chơi; số răng tùy chọn phải đứng trước "
                            "mention (mặc định 13, cho phép 2–25)."
                        ),
                        "crocodile challenge [số_răng] @user1 [@user2 @user3 @user4]",
                    ),
                    _entry(
                        "crocodile fire",
                        "Chủ phòng tạo lại bảng xác nhận hoặc bàn chơi của một ván còn mở.",
                        "crocodile fire <game_id>",
                    ),
                ),
                note=(
                    "Lời mời chờ phản hồi tối đa 5 phút. Mỗi lượt chọn một "
                    "răng; răng nguy hiểm kết thúc ván. Ván đang chơi hết hạn "
                    "sau 7 ngày không có lượt hợp lệ."
                ),
            ),
            HelpSection(
                name="Nối Từ",
                entries=(
                    _entry("noitu", "Xem luật trong kênh Nối Từ đã cấu hình."),
                    _entry("noitu status", "Xem từ hiện tại và các từ đã dùng."),
                    _entry(
                        "noitu hint",
                        "Nhận gợi ý; dùng trong kênh game, cooldown chung 30 giây.",
                    ),
                    _entry(
                        "noitu end",
                        "Đặt lại ván; bất kỳ member nào trong kênh game đều dùng được.",
                    ),
                    _entry(
                        "noitu analyze",
                        "Phân tích ván sau khi đã có ít nhất một nước đi của người chơi.",
                    ),
                ),
                note="Tin nhắn thường trong kênh cấu hình cũng được tính là lượt chơi.",
            ),
            HelpSection(
                name="Vua Tiếng Việt",
                entries=(
                    _entry("vtv", "Xem luật và câu đố trong kênh đã cấu hình."),
                    _entry("vtv status", "Xem trạng thái câu đố trong kênh game."),
                    _entry(
                        "vtv next",
                        "Thay câu đố hiện tại; bất kỳ member nào trong kênh game đều dùng được.",
                    ),
                    _entry(
                        "vtv hint",
                        "Mở một chữ; gần hết chữ sẽ kết thúc và tạo vòng mới.",
                    ),
                ),
                note="Tin nhắn thường trong kênh cấu hình cũng được kiểm tra đáp án.",
            ),
        ),
    ),
    HelpTopic(
        key="fun",
        label="Vui & tương tác",
        emoji="✨",
        option_description="Meter, avatar, tương tác và hôn nhân",
        title="Vui vẻ & tương tác xã hội",
        description="Các lệnh giải trí, tương tác thành viên và bảng xếp hạng.",
        color=0x9B59B6,
        sections=(
            HelpSection(
                name="Meter & thẻ vui",
                entries=(
                    _entry(
                        "gay",
                        "Gay meter; bỏ trống sẽ dùng chính bạn.",
                        "gay [@user]",
                    ),
                    _entry(
                        "les",
                        "Lesbian meter; bỏ trống sẽ dùng chính bạn.",
                        "les [@user]",
                        aliases=("les_meter", "lesbian"),
                    ),
                    _entry(
                        "penisize",
                        "Size meter; bỏ trống sẽ dùng chính bạn.",
                        "penisize [@user]",
                        aliases=("peni", "peni_size", "ppsize"),
                    ),
                    _entry(
                        "titansize",
                        "Meter số đo cm và cup hư cấu; bỏ trống sẽ dùng chính bạn.",
                        "titansize [@user]",
                    ),
                    _entry(
                        "femboycard",
                        (
                            "Tạo thẻ cho chính bạn kèm proof có chữ ký TFVN; "
                            "cần một role trong danh sách femboy; cooldown 10 giây."
                        ),
                    ),
                    _entry("ship", "Đo mức độ hợp đôi.", "ship @user1 @user2"),
                    _entry(
                        "redflag",
                        "Red/green flag meter; bỏ trống sẽ dùng chính bạn.",
                        "redflag [@user]",
                        aliases=("flags",),
                    ),
                    _grouped_entry(
                        (
                            ("aura", "aura [@user]"),
                            ("based", "based [@user]"),
                            ("brainrot", "brainrot [@user]"),
                            ("clown", "clown [@user]"),
                            ("cope", "cope [@user]"),
                            ("cringe", "cringe [@user]"),
                            ("delulu", "delulu [@user]"),
                            ("gyatt", "gyatt [@user]"),
                            ("ick", "ick [@user]"),
                            ("mainchar", "mainchar [@user]"),
                            ("npc", "npc [@user]"),
                            ("ohio", "ohio [@user]"),
                            ("rizz", "rizz [@user]"),
                            ("simp", "simp [@user]"),
                            ("skillissue", "skillissue [@user]"),
                            ("touchgrass", "touchgrass [@user]"),
                            ("yapper", "yapper [@user]"),
                        ),
                        "Các meter vui khác; nếu bỏ trống sẽ dùng chính bạn.",
                    ),
                ),
            ),
            HelpSection(
                name="Tương tác SFW",
                entries=(
                    _grouped_entry(
                        (
                            ("kiss", "kiss @user"),
                            ("hug", "hug @user"),
                            ("pat", "pat @user"),
                            ("slap", "slap @user"),
                            ("punch", "punch @user"),
                            ("hit", "hit @user"),
                            ("poke", "poke @user"),
                            ("cuddle", "cuddle @user"),
                            ("snuggle", "snuggle @user"),
                            ("boop", "boop @user"),
                            ("bonk", "bonk @user"),
                            ("stare", "stare @user"),
                            ("lick", "lick @user"),
                            ("smack", "smack @user"),
                            ("sniff", "sniff @user"),
                            ("kidnap", "kidnap @user"),
                        ),
                        "Tương tác SFW; cần @user, không nhận bot và cooldown 3 giây/lệnh.",
                    ),
                    _entry(
                        "handhold",
                        "Nắm tay member; không nhận bot, cooldown 3 giây.",
                        "handhold @user",
                        aliases=("holdhand",),
                    ),
                    _entry(
                        "bite",
                        "Cắn member; không nhận bot, cooldown 3 giây.",
                        "bite @user",
                        aliases=("nom",),
                    ),
                ),
                note=(
                    "Tự tương tác chỉ được phép với pat, slap, punch, hit, poke, bonk và "
                    "smack."
                ),
            ),
            HelpSection(
                name="Avatar, media & xếp hạng",
                entries=(
                    _entry(
                        "avatar",
                        "Xem global avatar; mặc định là chính bạn.",
                        "avatar [@user]",
                        aliases=(
                            "av",
                            "global_avatar",
                            "globalav",
                        ),
                    ),
                    _entry(
                        "server_avatar",
                        "Xem server avatar, fallback global; chỉ dùng trong server.",
                        "server_avatar [@user]",
                        aliases=(
                            "sav",
                            "guild_avatar",
                            "serverav",
                        ),
                    ),
                    _grouped_entry(
                        (("cat", "cat"), ("dog", "dog"), ("36", "36")),
                        "Ảnh động vật từ API và meme GIF ngẫu nhiên.",
                    ),
                    _grouped_entry(
                        (
                            ("rank", "rank"),
                            ("rank", "rank <action>"),
                            ("rank", "rank r"),
                            ("rank", "rank r <action>"),
                        ),
                        (
                            "BXH bot-wide toàn thời gian: chủ động/được tương tác. Action: "
                            "kiss, hug, pat, slap, punch, hit, poke, cuddle, snuggle, boop, "
                            "handhold, bonk, bite, stare, lick, smack, sniff, kidnap."
                        ),
                        aliases=("ranking",),
                    ),
                ),
            ),
            HelpSection(
                name="Hôn nhân",
                entries=(
                    _entry(
                        "propose",
                        (
                            "Cầu hôn member khác không phải bot; phản hồi trong 5 phút, "
                            "cooldown 30 giây."
                        ),
                        "propose @user",
                    ),
                    _entry(
                        "marriage",
                        "Xem hôn nhân của bạn hoặc một member trong server.",
                        "marriage [@user]",
                        aliases=("marry", "marriage_status"),
                    ),
                    _entry("marriage help", "Xem luật XP và hạng cặp đôi."),
                    _entry(
                        "marriage top",
                        "Top 10 cặp đôi theo XP trong server.",
                        aliases=(
                            "marriage lb",
                            "marriage leaderboard",
                            "marriage rank",
                        ),
                    ),
                    _entry(
                        "divorce",
                        "Ly hôn với xác nhận trong 60 giây; cooldown 30 giây.",
                    ),
                ),
                note=(
                    "Chỉ dùng trong server. Mỗi người chỉ có một hôn nhân hoặc lời cầu hôn "
                    "chờ xử lý."
                ),
            ),
        ),
    ),
    HelpTopic(
        key="utilities",
        label="Tiện ích & Booster",
        emoji="🧰",
        option_description="Quote, loa lớn và đặc quyền booster",
        title="Tiện ích & đặc quyền Booster",
        description="Công cụ dùng hằng ngày và tài nguyên riêng cho server booster.",
        color=0x1ABC9C,
        sections=(
            HelpSection(
                name="Tiện ích",
                entries=(
                    _entry(
                        "highlight",
                        "Xem điều kiện để tin nhắn được đưa lên highlight; chỉ dùng trong server.",
                    ),
                    _entry(
                        "quote",
                        (
                            "Quote reply/link/ID dạng embed; thêm `image` để tạo PNG; "
                            "cả hai dạng có proof có chữ ký; cooldown 5 giây/người."
                        ),
                        "quote [image] [message_link|message_id]",
                        aliases=("q", "quotes"),
                    ),
                    _entry(
                        "hash_verify",
                        (
                            "Kiểm tra mã proof ngắn và snapshot của Femboy Card "
                            "hoặc quote; vẫn nhận token tfv1 cũ; tối đa 3 lần/10 giây."
                        ),
                        "hash_verify <proof_code>",
                    ),
                    _entry(
                        "big_speaker",
                        "Nói lớn tối đa 180 ký tự; cooldown 30 giây.",
                        "big_speaker <cỡ 1-6> <nội dung>",
                        aliases=("loa", "speaker"),
                    ),
                    _entry(
                        "random_member",
                        "Chọn member được tag hoặc một member ngẫu nhiên trong role.",
                        "random_member <@member|@role>",
                    ),
                ),
                note="Giá big_speaker theo cỡ 1–6: 1 / 2 / 5 / 10 / 20 / 50 TC.",
            ),
            HelpSection(
                name="Dành cho Booster",
                entries=(
                    _entry(
                        "custom_role",
                        "Tạo role riêng; gọi không tham số để chọn màu/gradient bằng giao diện xem trước, hoặc dùng cú pháp cũ. Có thể đính kèm icon PNG ≤256 KiB.",
                        "custom_role <#RRGGBB[,#RRGGBB]> <tên role>",
                        aliases=("booster_role",),
                    ),
                    _entry(
                        "update_custom_role",
                        "Cập nhật role; gọi không tham số để mở giao diện màu có xem trước, hoặc dùng cú pháp cũ. Hỗ trợ icon PNG ≤256 KiB.",
                        "update_custom_role <#RRGGBB[,#RRGGBB]> <tên role>",
                        aliases=("customroleupdate", "boosterroleupdate"),
                    ),
                    _entry(
                        "custom_room",
                        "Tạo voice room riêng; gọi không tham số để chọn tên và giới hạn người bằng giao diện xem trước, hoặc nhập tên như cũ.",
                        "custom_room <tên phòng>",
                        aliases=("booster_room",),
                    ),
                ),
                note=(
                    "Mỗi booster có tối đa một custom role và một custom room; tên tối đa "
                    "100 ký tự và bot cần Manage Roles/Channels."
                ),
            ),
        ),
    ),
    HelpTopic(
        key="automation",
        label="Tính năng tự động",
        emoji="⚙️",
        option_description="Listener, lịch chạy, hệ thống nền và trạng thái bot",
        title="Tính năng tự động & chạy nền",
        description=(
            "Các hệ thống chạy nền và lệnh quản lý trạng thái bot. Chúng chỉ hoạt động "
            "khi cog tương ứng được bật và cấu hình đầy đủ."
        ),
        color=0x95A5A6,
        sections=(
            HelpSection(
                name="Thông báo & trạng thái",
                note=(
                    "• Gửi welcome; phân biệt tự rời, bị kick và bị ban trong cùng kênh BYE_CHANNEL.\n"
                    "• Đổi Discord activity ngẫu nhiên mỗi 5–15 phút khi không có trạng thái tạm thời.\n"
                    "• Thông báo sinh nhật một lần trong ngày tại BIRTHDAY_CHANNEL."
                ),
            ),
            HelpSection(
                name="Trạng thái bot — Administrator",
                entries=(
                    _entry("bot_status", "Mở bảng chọn activity và form nội dung/thời hạn."),
                    _entry(
                        "bot_status set",
                        "Đặt activity tạm thời cho toàn bot; thay thế trạng thái đã đặt.",
                        "bot_status set <type> <duration> <text>",
                    ),
                    _entry("bot_status show", "Xem trạng thái và thời điểm hết hạn."),
                    _entry("bot_status reset", "Kết thúc trạng thái tạm và đổi ngẫu nhiên ngay."),
                ),
                note=(
                    "Type: PLAYING, WATCHING, LISTENING, STREAMING, COMPETING, CUSTOM. "
                    "Duration: số nguyên + m/h/d, từ 1m đến 24h; text: 1–128 ký tự, một dòng. "
                    "Bảng có Đổi ngẫu nhiên / Làm mới / Đóng; chỉ người mở còn quyền Admin dùng được. "
                    "Bảng hết hạn sau 3 phút; đóng/hết hạn bảng không hủy trạng thái. "
                    "Admin ở bất kỳ server nào đều có thể đặt lại; "
                    "UI và set/reset chung cooldown 10 giây toàn bot. "
                    "Hết hạn trạng thái, reset, restart hoặc reload cog sẽ xoay ngẫu nhiên."
                ),
            ),
            HelpSection(
                name="AFK, nhắc việc & phản hồi",
                note=(
                    "• Ghi lại ai mention member đang AFK và tự xóa dynamic AFK khi họ trở lại.\n"
                    "• Kiểm tra lịch nhắc việc mỗi phút rồi gửi DM.\n"
                    "• Theo lịch ngủ UTC+7, tag member khi tới giờ và reply có tag cho "
                    "mọi tin họ gửi trước giờ dậy.\n"
                    "• Triggered reply khớp contains/exact, không phân biệt hoa thường "
                    "và không ping."
                ),
            ),
            HelpSection(
                name="An toàn & bảo trì",
                note=(
                    "• Bộ lọc từ cấm ghi log, cảnh cáo và xóa tin vi phạm.\n"
                    "• Area 51 theo dõi honeypot, cho phép hủy ban, tự dọn và gửi nhắc định kỳ.\n"
                    "• Booster janitor dọn custom role/room sau khi member ngừng boost."
                ),
            ),
            HelpSection(
                name="Dữ liệu tương tác",
                note=(
                    "• Giveaway, vote và lời cầu hôn được lên lịch/kết thúc tự động.\n"
                    "• Tin SFW đủ 5 💀 (không tính bot) được đăng ảnh chat vào HIGHLIGHT_CHANNEL, cách nhau tối thiểu 300 giây; bot reply chúc mừng tiếng Việt trên tin gốc; bỏ qua kênh NSFW.\n"
                    "• Nối Từ và Vua Tiếng Việt nhận đáp án từ tin nhắn thường trong kênh game.\n"
                    "• Tương tác SFW giữa vợ/chồng tự cộng XP và thông báo khi lên hạng."
                ),
            ),
            HelpSection(
                name="Theo mùa",
                note=(
                    "Lời chúc Tết Âm lịch 2026 chạy 16/02 17:00–17/02 17:00 UTC, "
                    "một lần duy nhất mỗi user trên toàn bot khi tin có `năm mới`, `nmvv`, "
                    "`2026` hoặc `new year`; hiện đã hết hiệu lực."
                ),
            ),
        ),
        always_available=True,
    ),
    HelpTopic(
        key="moderation",
        label="Quản trị",
        emoji="🛡️",
        option_description="Quyền, member, cases, cấu hình và vận hành",
        title="Quản trị & vận hành",
        description=(
            "Mọi người có thể đọc trang này; mỗi lệnh vẫn tự kiểm tra quyền Discord, "
            "role hierarchy, cấu hình và phạm vi server."
        ),
        color=0xE74C3C,
        sections=(
            HelpSection(
                name="Kỷ luật member",
                entries=(
                    _entry(
                        "kick",
                        "Mention kick ngay; reply mở bảng — Kick Members.",
                        "kick [@user] [lý do] · reply + kick",
                    ),
                    _entry(
                        "ban",
                        (
                            "Mention ban ngay, xóa tin 24 giờ; reply mở tùy chọn — Ban Members."
                        ),
                        "ban @user [lý do] · reply + ban",
                    ),
                    _entry(
                        "unban",
                        (
                            "ID unban ngay; reply mở tùy chọn — Ban Members; "
                            "mời lại cần Create Invite."
                        ),
                        "unban <user_id|@user> [lý do] · reply + unban",
                    ),
                    _entry(
                        "softban",
                        "Mention gán Tù ngay; reply mở bảng — Ban Members.",
                        "softban [@user] [lý do] · reply + softban",
                    ),
                    _entry(
                        "unsoftban",
                        "Mention khôi phục role ngay; reply mở bảng — Ban Members.",
                        "unsoftban [@user] [lý do] · reply + unsoftban",
                    ),
                ),
            ),
            HelpSection(
                name="Hạn chế & cảnh cáo",
                entries=(
                    _entry(
                        "mute",
                        "Mention mute ngay; reply mở bảng — Manage Roles.",
                        "mute [@user] [lý do] · reply + mute",
                    ),
                    _entry(
                        "unmute",
                        "Mention unmute ngay; reply mở bảng — Manage Roles.",
                        "unmute [@user] [lý do] · reply + unmute",
                    ),
                    _entry(
                        "timeout",
                        "Member + 1–40.320 phút timeout ngay; thiếu phút mở bảng — Moderate Members.",
                        "timeout [@user] [phút] [lý do] · reply + timeout",
                    ),
                    _entry(
                        "untimeout",
                        "Mention gỡ timeout ngay; reply mở bảng — Moderate Members.",
                        "untimeout [@user] [lý do] · reply + untimeout",
                    ),
                    _entry(
                        "warn",
                        "Mention lưu warning ngay; reply mở bảng — Manage Messages.",
                        "warn [@user] [lý do] · reply + warn",
                    ),
                    _entry(
                        "check_warn",
                        "Xem 10 cảnh cáo gần nhất; member có thể tự xem.",
                        "check_warn [@user]",
                    ),
                ),
                note=(
                    "Đủ đối số chạy ngay; reply không đối số mở bảng xác nhận. "
                    "Luôn kiểm tra quyền và thứ bậc role."
                ),
            ),
            HelpSection(
                name="Tên, role & xác minh",
                entries=(
                    _entry(
                        "nickchange",
                        "Member + tên đổi ngay; thiếu tên mở bảng — Manage Nicknames.",
                        "nickchange [@user] [nickname] · reply + nickchange",
                    ),
                    _entry(
                        "roleroll",
                        "Member + role gán ngay; thiếu role mở bảng — Manage Roles.",
                        "roleroll [@user] [tên role] · reply + roleroll",
                    ),
                    _entry(
                        "roleunroll",
                        "Member + role gỡ ngay; thiếu role mở bảng — Manage Roles.",
                        "roleunroll [@user] [tên role] · reply + roleunroll",
                    ),
                    _entry(
                        "rolecopy",
                        (
                            "Hai member copy ngay; reply đích mở bảng; "
                            "kết quả liệt kê role đã copy — Manage Roles."
                        ),
                        "rolecopy [@source] [@target] [lý do] · reply + rolecopy",
                    ),
                    _entry(
                        "verified",
                        "Gán role xác minh đã cấu hình — cần Manage Roles.",
                        "verified @user",
                    ),
                    _entry(
                        "unverified",
                        "Gỡ role xác minh và quyền kênh liên quan — cần Manage Roles.",
                        "unverified @user",
                    ),
                ),
            ),
            HelpSection(
                name="Tin nhắn & kênh",
                entries=(
                    _entry(
                        "purge",
                        "Có số lượng xóa 1–1.000 tin ngay; bỏ trống mở bảng — Manage Messages.",
                        "purge [số lượng]",
                    ),
                    _entry(
                        "purge_user",
                        "Member + số tin xóa ngay; thiếu số tin mở bảng — Manage Messages.",
                        "purge_user [@user] [số lượng] · reply + purge_user",
                    ),
                    _entry(
                        "clean_before",
                        "Có số ngày dọn ngay (1–3.650); bỏ trống mở bảng — Manage Messages.",
                        "clean_before [số ngày]",
                    ),
                    _entry(
                        "slowmode",
                        "Nhóm hướng dẫn kiểm tra và quản lý slowmode.",
                    ),
                    _entry(
                        "slowmode check_bypass",
                        "Kiểm tra overwrite của bạn hoặc member trong kênh.",
                        "slowmode check_bypass [@user]",
                    ),
                    _entry(
                        "slowmode immune",
                        "Mention để cấp bypass ngay; reply mở bảng — Manage Roles.",
                        "slowmode immune [@user] [lý do]",
                    ),
                    _entry(
                        "slowmode prominent",
                        "Mention để gỡ bypass ngay; reply mở bảng — Manage Roles.",
                        "slowmode prominent [@user] [lý do]",
                    ),
                ),
            ),
            HelpSection(
                name="Moderation cases",
                entries=(
                    _entry("case", "Xem hướng dẫn case — cần Manage Messages."),
                    _entry(
                        "case view",
                        "Xem case theo số — cần Manage Messages.",
                        "case view <số>",
                    ),
                    _entry(
                        "case history",
                        "Xem 1–10 case gần nhất của member — cần Manage Messages.",
                        "case history @user [limit]",
                    ),
                    _entry(
                        "case edit",
                        "Có lý do sửa ngay; bỏ trống mở bảng; chặn ghi đè cũ — Manage Messages.",
                        "case edit <số> [lý do]",
                    ),
                    _entry(
                        "case status",
                        "Có trạng thái đổi ngay; bỏ trống mở bảng — Manage Messages.",
                        "case status <số> [open|resolved|appealed|void]",
                    ),
                    _entry(
                        "case log_channel",
                        "Có text channel đổi log ngay; bỏ trống mở bảng — Manage Server.",
                        "case log_channel [#channel]",
                    ),
                ),
            ),
            HelpSection(
                name="Chẩn đoán & cấu hình",
                entries=(
                    _entry(
                        "setup",
                        "Chạy chẩn đoán đầy đủ — cần Manage Server, cooldown 15 giây.",
                        aliases=("diagnose",),
                    ),
                    _entry(
                        "setup check",
                        "Kiểm tra DB, cog, IDs, permission và role hierarchy — Manage Server.",
                    ),
                    _entry(
                        "server_stats",
                        (
                            "Uptime và số command/error từ lúc chạy — Administrator, "
                            "cooldown 10 giây/server."
                        ),
                    ),
                    _entry(
                        "operation_dashboard",
                        (
                            "Doctor/audit — Admin; chủ bot xem/rời server khác "
                            "+ lịch sử kết nối."
                        ),
                    ),
                    _entry(
                        "leave",
                        "Cho bot rời server ngay, không xác nhận — Administrator.",
                    ),
                    _entry("setting", "Xem hướng dẫn biến runtime — Administrator."),
                    _entry(
                        "setting set_variable",
                        "Đặt STRING/ARRAY qua hội thoại — Administrator.",
                        "setting set_variable <NAME>",
                    ),
                    _entry(
                        "setting get_variable",
                        "Đọc biến runtime — Administrator.",
                        "setting get_variable <NAME>",
                    ),
                ),
            ),
            HelpSection(
                name="Trap Coin & shop admin",
                entries=(
                    _entry(
                        "add_tc",
                        "Cộng 1–1.000.000.000 TC — Administrator.",
                        "add_tc @user <số> [lý do]",
                        aliases=("give_tc", "grant_tc"),
                    ),
                    _entry(
                        "remove_tc",
                        "Trừ 1–1.000.000.000 TC nếu đủ số dư — Administrator.",
                        "remove_tc @user <số> [lý do]",
                        aliases=("sub_tc", "subtract_tc", "take_tc"),
                    ),
                    _entry(
                        "set_tc",
                        "Đặt số dư 0–1.000.000.000 TC — Administrator.",
                        "set_tc @user <số> [lý do]",
                        aliases=("set_balance",),
                    ),
                    _entry(
                        "check_tc",
                        "Xem số dư member — Administrator.",
                        "check_tc [@user]",
                        aliases=("tc_balance",),
                    ),
                    _entry(
                        "shop add_role",
                        "Thêm role giá 1–1.000.000.000 TC vào shop — cần Manage Server.",
                        "shop add_role <id> <giá> @role [mô tả]",
                    ),
                    _entry(
                        "shop add_badge",
                        "Thêm badge giá 1–1.000.000.000 TC — cần Manage Server.",
                        "shop add_badge <id> <giá> <tên nhiều từ>",
                    ),
                    _entry(
                        "shop remove",
                        "Ẩn vật phẩm khỏi shop — cần Manage Server.",
                        "shop remove <item_id>",
                        aliases=("shop disable",),
                    ),
                ),
                note=(
                    "Bốn lệnh số dư không nhận bot. Item ID dài 1–32 ký tự, bắt đầu bằng "
                    "chữ/số và chỉ gồm chữ thường, số, `_`, `-`."
                ),
            ),
            HelpSection(
                name="Triggered replies",
                entries=(
                    _entry(
                        "triggerreply",
                        "Xem hướng dẫn rule — guild Administrator.",
                        aliases=("autoreply",),
                    ),
                    _entry(
                        "triggerreply add",
                        "Thêm rule; tối đa 100 rule/server — Administrator.",
                        "triggerreply add <contains|include|exact> <cụm từ> | <trả lời>",
                    ),
                    _entry(
                        "triggerreply update",
                        "Sửa rule nhưng giữ nguyên ID — Administrator.",
                        "triggerreply update <ID> <contains|exact> <cụm từ> | <trả lời>",
                        aliases=("triggerreply edit",),
                    ),
                    _entry(
                        "triggerreply list",
                        "Liệt kê rule và ID — Administrator.",
                    ),
                    _entry(
                        "triggerreply remove",
                        "Xóa rule theo ID — Administrator.",
                        "triggerreply remove <ID>",
                        aliases=("triggerreply delete",),
                    ),
                ),
                note=(
                    "Alias cha `autoreply` dùng được với mọi subcommand, gồm "
                    "`autoreply add`, `autoreply update/edit`, `autoreply list` và "
                    "`autoreply remove/delete`. "
                    "Trigger tối đa 200 ký tự; reply tối đa "
                    "2.000 ký tự và không ping."
                ),
            ),
            HelpSection(
                name="Media & Area 51",
                entries=(
                    _entry(
                        "save_image",
                        "Lưu ảnh đính kèm và metadata — cần Manage Messages.",
                        "save_image <collection> [key value ...]",
                    ),
                    _entry(
                        "area51_fire",
                        "Preview đích và xác nhận gửi cảnh báo Area 51 — Administrator.",
                        aliases=("area51_bump_now", "area51_remind_now"),
                    ),
                ),
            ),
        ),
    ),
    HelpTopic(
        key="nsfw",
        label="NSFW",
        emoji="🔞",
        option_description="Nội dung và tương tác chỉ dành cho kênh NSFW",
        title="Lệnh NSFW",
        description=(
            "Chỉ sử dụng trong kênh Discord được đánh dấu NSFW. Các lệnh vẫn áp dụng "
            "role lock và giới hạn riêng."
        ),
        color=0xE91E63,
        sections=(
            HelpSection(
                name="Tìm kiếm & luật",
                entries=(
                    _entry("r34", "Tìm nội dung Rule34 bằng tag bắt buộc.", "r34 <tags>"),
                    _entry("gbr", "Tìm nội dung Gelbooru bằng tag bắt buộc.", "gbr <tags>"),
                    _entry("nsfwrule", "Xem luật cho các tương tác NSFW."),
                ),
            ),
            HelpSection(
                name="Tương tác",
                entries=(
                    _grouped_entry(
                        (
                            ("bj", "bj @user"),
                            ("rj", "rj @user"),
                            ("hj", "hj @user"),
                            ("fj", "fj @user"),
                            ("spank", "spank @user"),
                            ("frot", "frot @user"),
                            ("fuck", "fuck @user"),
                            ("cream", "cream @user"),
                        ),
                        "Tương tác một mục tiêu; cooldown 3 giây mỗi lệnh.",
                    ),
                    _entry(
                        "aj",
                        "Tương tác một mục tiêu; cooldown 3 giây.",
                        "aj @user",
                        aliases=("assjob",),
                    ),
                    _entry(
                        "tj",
                        "Tương tác một mục tiêu; cooldown 3 giây.",
                        "tj @user",
                        aliases=("thighjob",),
                    ),
                    _entry(
                        "3some",
                        "Hai mục tiêu khác nhau, không gồm người gọi.",
                        "3some @user1 @user2",
                        aliases=("threesome",),
                    ),
                    _entry(
                        "orgy",
                        "Mục tiêu phải khác nhau và không gồm người gọi.",
                        "orgy @user1 @user2 [@user3 ... @user10]",
                    ),
                ),
                note=(
                    "Chỉ hj và spank cho phép tự target. NSFW lock chỉ chặn 12 lệnh "
                    "tương tác trong mục này."
                ),
            ),
            HelpSection(
                name="Xếp hạng & quyền truy cập",
                entries=(
                    _grouped_entry(
                        (
                            ("ranknsfw", "ranknsfw"),
                            ("ranknsfw", "ranknsfw <action>"),
                            ("ranknsfw", "ranknsfw r"),
                            ("ranknsfw", "ranknsfw r <action>"),
                        ),
                        (
                            "BXH bot-wide tháng UTC hiện tại: chủ động/được tương tác. "
                            "Action: bj, rj, hj, fj, aj, tj, spank, frot, fuck, cream, "
                            "3some, orgy."
                        ),
                        aliases=("nsfwrank",),
                    ),
                    _entry(
                        "mrank",
                        "Top 5 hai chiều của tháng chỉ định — Administrator.",
                        "mrank <tháng> <năm>",
                    ),
                    _entry(
                        "locknsfw",
                        "Queen khóa tương tác NSFW của member 24 giờ; cooldown 3 ngày.",
                        "locknsfw @user",
                    ),
                    _entry(
                        "unlocknsfw",
                        "Queen gỡ một lock còn hiệu lực do chính mình tạo.",
                    ),
                    _entry(
                        "self_unverified",
                        (
                            "Tự gỡ role xác minh sau Yes/No; hết hạn phải gọi lại; "
                            "muốn vào lại hỏi min mót."
                        ),
                    ),
                ),
                note="King role nhân 3 điểm tương tác; chỉ Queen role dùng lock/unlock.",
            ),
        ),
    ),
)

HELP_TOPIC_MAP = {topic.key: topic for topic in HELP_TOPICS}
HELP_TOPIC_ALIASES = {
    "overview": "overview",
    "general": "overview",
    "tong quan": "overview",
    "tổng quan": "overview",
    "community": "community",
    "cong dong": "community",
    "cộng đồng": "community",
    "economy": "economy",
    "trap coin": "economy",
    "shop": "economy",
    "cultivation": "cultivation",
    "cultivate": "cultivation",
    "tutien": "cultivation",
    "tu tien": "cultivation",
    "tu tiên": "cultivation",
    "tien lo": "cultivation",
    "tiên lộ": "cultivation",
    "games": "games",
    "game": "games",
    "tro choi": "games",
    "trò chơi": "games",
    "fun": "fun",
    "social": "fun",
    "vui": "fun",
    "utilities": "utilities",
    "utility": "utilities",
    "tools": "utilities",
    "booster": "utilities",
    "automation": "automation",
    "automatic": "automation",
    "tu dong": "automation",
    "tự động": "automation",
    "moderation": "moderation",
    "mod": "moderation",
    "admin": "moderation",
    "quan tri": "moderation",
    "quản trị": "moderation",
    "nsfw": "nsfw",
}


def resolve_help_topic(value: str | None) -> str | None:
    """Resolve a user-facing topic name to its stable dropdown key."""
    if value is None:
        return None
    normalized = " ".join(value.strip().lower().replace("_", " ").split())
    return HELP_TOPIC_ALIASES.get(normalized)


def _format_command(prefix: str, usage: HelpUsage) -> str:
    return f"`{prefix}{usage.text}`"


def _available_usages(
    entry: HelpEntry,
    available_commands: frozenset[str] | None,
) -> tuple[HelpUsage, ...]:
    if available_commands is None:
        return entry.usages
    return tuple(
        usage for usage in entry.usages if usage.command_name in available_commands
    )


def render_help_section(
    section: HelpSection,
    prefix: str,
    available_commands: frozenset[str] | None = None,
) -> str | None:
    """Render one help section, optionally filtering canonical command names."""
    lines = []
    for entry in section.entries:
        usages = _available_usages(entry, available_commands)
        if not usages:
            continue
        command_list = ", ".join(_format_command(prefix, usage) for usage in usages)
        if entry.aliases:
            aliases = ", ".join(f"`{alias}`" for alias in entry.aliases)
            command_list += f" (alias: {aliases})"
        lines.append(f"{command_list} — {entry.description}")

    if section.note:
        lines.append(section.note)
    return "\n".join(lines) if lines else None


def topic_has_available_commands(
    topic: HelpTopic,
    available_commands: frozenset[str] | None,
) -> bool:
    if available_commands is None or topic.always_available:
        return True
    return any(
        _available_usages(entry, available_commands)
        for section in topic.sections
        for entry in section.entries
    )


def available_help_topics(
    available_commands: frozenset[str] | None,
    *,
    allow_nsfw: bool,
    always_include: tuple[str, ...] = (),
) -> tuple[HelpTopic, ...]:
    """Return help topics allowed for this channel and optional command profile."""
    included = set(always_include)
    topics = []
    for topic in HELP_TOPICS:
        if topic.key == "nsfw" and not allow_nsfw:
            continue
        if (
            topic.key != "overview"
            and topic.key not in included
            and not topic_has_available_commands(topic, available_commands)
        ):
            continue
        topics.append(topic)
    return tuple(topics)


def build_help_embed(
    topic_key: str,
    prefix: str,
    *,
    available_commands: frozenset[str] | None = None,
    beta_commands: tuple[str, ...] = (),
) -> discord.Embed:
    """Build a bounded help embed; unknown topic keys safely use the overview."""
    topic = HELP_TOPIC_MAP.get(topic_key, HELP_TOPIC_MAP["overview"])
    embed = discord.Embed(
        title=f"{topic.emoji} {topic.title}",
        description=topic.description,
        color=topic.color,
    )

    for section in topic.sections:
        value = render_help_section(section, prefix, available_commands)
        if value is not None:
            embed.add_field(name=section.name, value=value, inline=False)

    if not embed.fields:
        embed.description += "\n\nHiện không có lệnh nào thuộc chủ đề này được tải."

    if topic.key == "overview" and beta_commands:
        shown_commands = beta_commands[:8]
        value = ", ".join(f"`{prefix}{name}`" for name in shown_commands)
        if len(beta_commands) > len(shown_commands):
            value += f"\n… và {len(beta_commands) - len(shown_commands)} lệnh Beta khác."
        value += "\nChỉ thành viên có Beta role đã cấu hình mới dùng được."
        embed.add_field(name="🧪 Beta access", value=value, inline=False)

    display_prefix = prefix.rstrip() or prefix
    embed.set_footer(
        text=f"Prefix: {display_prefix} • Menu chỉ dành cho người đã gọi lệnh"
    )
    return embed


def _is_nsfw_channel(channel: object) -> bool:
    is_nsfw = getattr(channel, "is_nsfw", None)
    return bool(is_nsfw()) if callable(is_nsfw) else False


class HelpTopicSelect(discord.ui.Select):
    def __init__(self, help_view: "HelpView") -> None:
        self.help_view = help_view
        options = [
            discord.SelectOption(
                label=topic.label,
                value=topic.key,
                description=topic.option_description,
                emoji=topic.emoji,
                default=topic.key == help_view.selected_topic_key,
            )
            for topic in help_view.topics
        ]
        super().__init__(
            custom_id=HELP_SELECT_CUSTOM_ID,
            placeholder=HELP_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options,
        )

    def set_selected(self, topic_key: str) -> None:
        for option in self.options:
            option.default = option.value == topic_key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.help_view.select_topic(self.values[0], interaction)


class HelpView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        topics: tuple[HelpTopic, ...],
        selected_topic_key: str,
        prefix: str,
        available_commands: frozenset[str] | None,
        beta_commands: tuple[str, ...],
        allow_nsfw: bool,
    ) -> None:
        super().__init__(timeout=HELP_MENU_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.topics = topics
        self.topic_map = {topic.key: topic for topic in topics}
        self.selected_topic_key = (
            selected_topic_key if selected_topic_key in self.topic_map else "overview"
        )
        self.prefix = prefix
        self.available_commands = available_commands
        self.beta_commands = beta_commands
        self.allow_nsfw = allow_nsfw
        self.message: discord.Message | None = None
        self.topic_select = HelpTopicSelect(self)
        self.add_item(self.topic_select)

    def current_embed(self) -> discord.Embed:
        return build_help_embed(
            self.selected_topic_key,
            self.prefix,
            available_commands=self.available_commands,
            beta_commands=self.beta_commands,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Chỉ người đã mở menu trợ giúp mới có thể đổi chủ đề.",
            ephemeral=True,
        )
        return False

    async def select_topic(
        self,
        topic_key: str,
        interaction: discord.Interaction,
    ) -> None:
        if topic_key not in self.topic_map:
            await interaction.response.send_message(
                "Chủ đề trợ giúp này không còn khả dụng. Hãy mở lại menu.",
                ephemeral=True,
            )
            return

        if topic_key == "nsfw" and (
            not self.allow_nsfw or not _is_nsfw_channel(interaction.channel)
        ):
            await interaction.response.send_message(
                "Chủ đề NSFW chỉ khả dụng trong kênh được đánh dấu NSFW.",
                ephemeral=True,
            )
            return

        self.selected_topic_key = topic_key
        self.topic_select.set_selected(topic_key)
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _command_is_visible(command: commands.Command) -> bool:
        current: commands.Command | None = command
        while current is not None:
            if current.hidden or not current.enabled:
                return False
            current = current.parent
        return True

    def _available_beta_commands(self, ctx: commands.Context) -> tuple[str, ...]:
        if beta_access_denial(ctx) is not None:
            return ()
        return tuple(
            sorted(
                command.qualified_name
                for command in self.bot.walk_commands()
                if self._command_is_visible(command) and is_beta_function(command)
            )
        )

    def _context_prefix(self, ctx: commands.Context) -> str:
        clean_prefix = getattr(ctx, "clean_prefix", None)
        if isinstance(clean_prefix, str) and clean_prefix:
            return clean_prefix
        command_prefix = getattr(self.bot, "command_prefix", "!tf ")
        return command_prefix if isinstance(command_prefix, str) else "!tf "

    async def _send_help_menu(
        self,
        ctx: commands.Context,
        *,
        initial_topic_key: str = "overview",
        allow_nsfw: bool | None = None,
    ) -> None:
        if allow_nsfw is None:
            allow_nsfw = _is_nsfw_channel(ctx.channel)

        # Help is the full bot catalog, even in a partial development profile.
        available_commands = None
        topics = available_help_topics(
            available_commands,
            allow_nsfw=allow_nsfw,
            always_include=(initial_topic_key,),
        )
        if initial_topic_key not in {topic.key for topic in topics}:
            initial_topic_key = "overview"

        view = HelpView(
            author_id=ctx.author.id,
            topics=topics,
            selected_topic_key=initial_topic_key,
            prefix=self._context_prefix(ctx),
            available_commands=available_commands,
            beta_commands=self._available_beta_commands(ctx),
            allow_nsfw=allow_nsfw,
        )
        view.message = await ctx.reply(
            embed=view.current_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=True,
        )

    @commands.command(
        name="help",
        help="Mở menu trợ giúp theo chủ đề.",
    )
    async def custom_help(
        self,
        ctx: commands.Context,
        *,
        topic: str | None = None,
    ) -> None:
        topic_key = resolve_help_topic(topic) or "overview"
        allow_nsfw = _is_nsfw_channel(ctx.channel)

        if topic_key == "nsfw" and not allow_nsfw:
            await ctx.reply("🔞 Hãy mở chủ đề này trong kênh NSFW.")
            return

        await self._send_help_menu(
            ctx,
            initial_topic_key=topic_key,
            allow_nsfw=allow_nsfw,
        )

    @commands.command(
        name="mod",
        help="Mở menu trợ giúp quản trị; từng lệnh tự kiểm tra quyền.",
    )
    async def mod_help(self, ctx: commands.Context) -> None:
        await self._send_help_menu(
            ctx,
            initial_topic_key="moderation",
        )

    @commands.command(
        name="nsfw",
        help="Mở menu trợ giúp NSFW trong kênh phù hợp.",
    )
    async def nsfw_help(self, ctx: commands.Context) -> None:
        if not _is_nsfw_channel(ctx.channel):
            try:
                await ctx.message.add_reaction("⚠️")
            except discord.HTTPException:
                pass
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            try:
                await warn_msg.delete()
            except discord.HTTPException:
                pass
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        await self._send_help_menu(
            ctx,
            initial_topic_key="nsfw",
            allow_nsfw=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
