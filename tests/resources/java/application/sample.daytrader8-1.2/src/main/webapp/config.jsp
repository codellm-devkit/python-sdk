<!-- 
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
-->
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META http-equiv="Content-Style-Type" content="text/css">
<link rel="shortcut icon" href="./favicon.ico" />
<TITLE>Welcome to DayTrader</TITLE>
</HEAD>
<BODY bgcolor="#ffffff" link="#000099">
    <%@ page
        import="com.ibm.websphere.samples.daytrader.util.TradeConfig"
        session="false" isThreadSafe="true" isErrorPage="false"%>
    <TABLE style="font-size: smaller">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10"><B><FONT color="#ffffff">DayTrader
                            Configuration</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR>
                <TD colspan="6">
                    <HR>
                </TD>
            </TR>
            <TR>
                <TD colspan="6"></TD>
            </TR>
        </TBODY>
    </TABLE>

    <%
        String status;
        status = (String) request.getAttribute("status");
        if (status != null) {
    %>
    <TABLE width="740" height="30">
        <TBODY>
            <TR>
                <TD></TD>
                <TD><FONT color="#ff0033"> <%
     out.print(status);
 %>
                </FONT></TD>
                <TD></TD>
            </TR>
        </TBODY>
    </TABLE>
    <%
        }
    %>

    <FORM action="config" method="POST">
        <INPUT type="hidden" name="action" value="updateConfig">

        <TABLE border="1" width="740">
            <TBODY>
                <TR>
                    <TD colspan="2">The current DayTrader runtime
                        configuration is detailed below. View and
                        optionally update run-time parameters. &nbsp;<BR>
                        <BR> <B>NOTE: </B>Parameters settings will
                        return to default on&nbsp;server restart. To
                        make configuration settings persistent across
                        application server stop/starts, edit the daytrader.props
                        file inside daytrader-ee7-web.war (which is inside the 
                        daytrader ear file).<BR>
                        <HR>
                    </TD>
                </TR>
                <TR>
                    <TD width="200" align="left"><B>Run-Time Mode </B>
                        <P align="left">
                            <%
                                String configParm = "RunTimeMode";
                                String names[] = TradeConfig.getRunTimeModeNames();
                                int index = TradeConfig.getRunTimeMode();
                                for (int i = 0; i < names.length; i++) {
                                    out.print("<INPUT type=\"radio\" disabled=\"true\" name=\"" + configParm + "\" value=\"" + i + "\" ");
                                    if (index == i)
                                        out.print("checked");
                                    out.print("> " + names[i] + "<BR>");
                                }
                            %>
                        </P></TD>
                    <TD><BR> Run Time Mode determines server
                        implementation of the TradeServices to use in
                        the DayTrader application Enterprise Java Beans
                        including Session, Entity and Message beans or
                        Direct mode which uses direct database and JMS
                        access. See <A href="docs/tradeFAQ.html">DayTrader
                            FAQ</A> for details.<BR></TD>
                </TR>
                <TR>
                    <TD align="left"><B>Order-Processing Mode </B>
                        <P align="left">
                            <%
                                configParm = "OrderProcessingMode";
                                names = TradeConfig.getOrderProcessingModeNames();
                                index = TradeConfig.getOrderProcessingMode();
                                for (int i = 0; i < names.length; i++) {
                                    out.print("<INPUT type=\"radio\" name=\"" + configParm + "\" value=\"" + i + "\" ");
                                    if (index == i)
                                        out.print("checked");
                                    out.print("> " + names[i] + "<BR>");
                                }
                            %>
                        </P></TD>
                    <TD><BR> Order Processing Mode determines
                        the mode for completing stock purchase and sell
                        operations. Synchronous mode completes the order
                        immediately. Asychronous_2-Phase performs a
                        2-phase commit over the EJB Entity/DB and
                        MDB/JMS transactions. See <A
                        href="docs/tradeFAQ.html">DayTrader FAQ</A> for
                        details. <B></B><BR></TD>
                </TR>
                <TR>
                    <TD align="left"><B>WebInterface</B>
                        <P align="left">
                            <%
                                configParm = "WebInterface";
                                names = TradeConfig.getWebInterfaceNames();
                                index = TradeConfig.getWebInterface();
                                for (int i = 0; i < names.length; i++) {
                                    out.print("<INPUT type=\"radio\" name=\"" + configParm + "\" value=\"" + i + "\" ");
                                    if (index == i)
                                        out.print("checked");
                                    out.print("> " + names[i] + "<BR>");
                                }
                            %>
                        </P></TD>
                    <TD>This setting determines the Web interface
                        technology used, JSPs or JSPs with static images
                        and GIFs.</TD>
                </TR>
                <TR>
                    <TD colspan="2" align="center"><B>Miscellaneous
                            Settings</B></TD>
                </TR>
                <TR>
                    <TD align="left"><B>DayTrader Max Users </B><BR>
                        <INPUT size="25" type="text" name="MaxUsers"
                        value="<%=TradeConfig.getMAX_USERS()%>"><BR>
                        <B>Trade Max Quotes</B><BR> <INPUT
                        size="25" type="text" name="MaxQuotes"
                        value="<%=TradeConfig.getMAX_QUOTES() %>"></TD>
                    <TD>By default the DayTrader database is
                        populated with 15,000 users (uid:0 - uid:199)
                        and 10,000 quotes (s:0 - s:399). <BR>
                    </TD>
                </TR>
                <TR>
                    <TD align="left"><B>Market Summary Interval</B><BR>
                        <INPUT size="25" type="text"
                        name="marketSummaryInterval"
                        value="<%=TradeConfig.getMarketSummaryInterval()%>"></TD>
                    <TD>&lt; 0 Do not perform Market Summary
                        Operations. <br>= 0 Perform market Summary
                        on every request.<br> <br>&gt; 0 number of
                        seconds between Market Summary Operations<br>
                    </TD>
                </TR>
                <TR>
                    <TD align="left"><B>Primitive Iteration</B><BR>
                        <INPUT size="25" type="text"
                        name="primIterations"
                        value="<%=TradeConfig.getPrimIterations()%>"></TD>
                    <TD>By default the DayTrader primitives are
                        execute one operation per web request. Change
                        this value to repeat operations multiple times
                        per web request.</TD>
                </TR>
                <TR>
                    <TD align="left"><INPUT type="checkbox"
                        <%=TradeConfig.getPublishQuotePriceChange() ? "checked" : ""%>
                        name="EnablePublishQuotePriceChange"> <B><FONT
                            size="-1">Publish Quote Updates</FONT></B><BR></TD>
                    <TD>Publish quote price changes to a JMS topic.<BR>
                    </TD>
                </TR>
                <TR>
                    <TD align="left"><B>Percent of Quote Price Changes to List</B><BR>
                        <INPUT size="25" type="text"
                        name="ListQuotePriceChangeFrequency"
                        value="<%=TradeConfig.getListQuotePriceChangeFrequency()%>"></TD>
                    <TD>The percent of recent trades to display on the Market Summary websocket.</TD>
                 </TR>
                 <TR>
                    <TD align="left"><INPUT type="checkbox"
                        <%=TradeConfig.getDisplayOrderAlerts() ? "checked" : ""%>
                        name="DisplayOrderAlerts"> <B><FONT
                            size="-1">Display Order Alerts</FONT></B><BR></TD>
                    <TD>Display completed order alerts.<BR>
                    </TD>
                </TR>
                <TR>
                    <TD align="left"><INPUT type="checkbox"
                        <%=TradeConfig.getLongRun() ? "checked" : ""%>
                        name="EnableLongRun"> <B><FONT
                            size="-1">Enable long run support</FONT></B><BR></TD>
                    <TD>Enable long run support by disabling the
                        show all orders query performed on the Account
                        page.<BR>
                    </TD>
                </TR>
                <TR>
                    <TD colspan="2" align="right"><INPUT
                        type="submit" value="Update Config">
                    </TD>
                </TR>
            </TBODY>
        </TABLE>

        <TABLE width="740" height="54" style="font-size: smaller">
            <TBODY>
                <TR>
                    <TD colspan="2">
                        <HR>
                    </TD>
                </TR>
                <TR>
                    <TD colspan="2"></TD>
                </TR>
                <TR>
                    <TD bgcolor="#c93333" align="left" width="640"
                        height="10"><B><FONT color="#ffffff">DayTrader
                                Configuration</FONT></B></TD>
                    <TD align="center" bgcolor="#000000" width="100"
                        height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
                </TR>
            </TBODY>
        </TABLE>
    </FORM>
</BODY>
</HTML>
