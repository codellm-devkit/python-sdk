<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
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
<HTML>
<HEAD>
<META http-equiv="Content-Style-Type" content="text/css">
<TITLE>Welcome to Trade</TITLE>
</HEAD>
<BODY bgcolor="#ffffff" link="#000099">
    <%@ page
        import="com.ibm.websphere.samples.daytrader.util.TradeConfig"
        session="false" isThreadSafe="true" isErrorPage="false"%>

    <jsp:useBean
        class="com.ibm.websphere.samples.daytrader.beans.RunStatsDataBean"
        id="runStatsData" scope="request" />
    <%
        double loginPercentage = (double) ((TradeConfig.getScenarioMixes())[0][TradeConfig.LOGOUT_OP]) / 100.0;
        double logoutPercentage = (double) ((TradeConfig.getScenarioMixes())[0][TradeConfig.LOGOUT_OP]) / 100.0;
        double buyOrderPercentage = (double) ((TradeConfig.getScenarioMixes())[0][TradeConfig.BUY_OP]) / 100.0;
        double sellOrderPercentage = (double) ((TradeConfig.getScenarioMixes())[0][TradeConfig.SELL_OP]) / 100.0;
        double orderPercentage = buyOrderPercentage + sellOrderPercentage;
        double registerPercentage = (double) ((TradeConfig.getScenarioMixes())[0][TradeConfig.REGISTER_OP]) / 100.0;

        int logins = runStatsData.getSumLoginCount() - runStatsData.getTradeUserCount(); //account for each user being logged in up front
        if (logins < 0)
            logins = 0; //no requests before reset
        //double expectedRequests = ((double) logins) / loginPercentage;
        double expectedRequests = (double) TradeConfig.getScenarioCount();
        TradeConfig.setScenarioCount(0);

        int verifyPercent = TradeConfig.verifyPercent;
    %>
    <%!// verifies 2 values are w/in tradeConfig.verifyPercent percent
    String verify(double expected, double actual, int verifyPercent) {
        String retVal = "";
        if ((expected == 0.0) || (actual == 0.0))
            return "N/A";
        double check = (actual / expected) * 100 - 100;
        //PASS
        retVal += check + "% ";
        if ((check >= (-1.0 * verifyPercent)) && (check <= verifyPercent))
            retVal += " Pass";
        else
            retVal += " Fail<SUP>4</SUP>";
        if (check > 0.0)
            retVal = "+" + retVal;
        //System.out.println("verify --- expected="+expected+" actual="+actual+ " check="+check);		
        return retVal;
    }

    String verify(int expected, int actual, int verifyPercent) {
        return verify((double) expected, (double) actual, verifyPercent);
    }%>
    <DIV align="left">

        <TABLE style="font-size: smaller">
            <TBODY>
                <TR>
                    <TD bgcolor="#c93333" align="left" width="640"
                        height="10" colspan=5><B><FONT
                            color="#ffffff">DayTrader Scenario
                                Runtime Statistics</FONT></B></TD>
                    <TD align="center" bgcolor="#000000" width="100"
                        height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
                </TR>
            </TBODY>
        </TABLE>
        <TABLE width="639" height="30">
            <TBODY>
                <TR>
                    <TD width="228"><B><FONT size="-1"
                            color="#cc0000"> <%
     String status;
     status = (String) request.getAttribute("status");
     if (status != null)
         out.print(status);
 %>
                        </FONT></B></TD>
                    <TD width="202"></TD>
                    <TD width="187" align="right"><FONT size="-1"><A
                            href="config" target="_self">Modify
                                runtime configuration</A></FONT></TD>
                </TR>
            </TBODY>
        </TABLE>
    </DIV>
    <TABLE width="645">
        <TBODY>
            <TR>
                <TD valign="top" width="643">
                    <TABLE width="100%">
                        <TBODY>
                            <TR align="center">
                                <TD colspan="6">
                                    <CENTER></CENTER>
                                    <TABLE border="1"
                                        style="font-size: smaller"
                                        cellpadding="2" cellspacing="0">
                                        <COL span="1" align="right">
                                        <COL span="1">
                                        <COL span="3" align="center">
                                        <CAPTION align="bottom">
                                            <FONT size="+1"><B>Benchmark
                                                    scenario statistics</B></FONT>
                                        </CAPTION>
                                        <TBODY>
                                            <TR bgcolor="#f3f3f3">
                                                <TD colspan="2"
                                                    align="center"><B>Benchmark
                                                        runtime
                                                        configuration
                                                        summary</B></TD>
                                                <TD colspan="3"><B>Value
                                                </B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="2"><A
                                                    href="docs/glossary.html">Run-Time
                                                        Mode</A></TD>
                                                <TD colspan="3"><B><%=(TradeConfig.getRunTimeModeNames())[TradeConfig.getRunTimeMode()]%></B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="2"><A
                                                    href="docs/glossary.html">Order-Processing
                                                        Mode</A></TD>
                                                <TD colspan="3"><B><%=(TradeConfig.getOrderProcessingModeNames())[TradeConfig.getOrderProcessingMode()]%></B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="2"><A
                                                    href="docs/glossary.html">Web
                                                        Interface</A></TD>
                                                <TD colspan="3"><B><%=(TradeConfig.getWebInterfaceNames())[TradeConfig.getWebInterface()]%></B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="2"><A
                                                    href="docs/glossary.html">Active
                                                        Traders / Trade
                                                        User population</A></TD>
                                                <TD colspan="3"><B><%=runStatsData.getTradeUserCount()%>
                                                        / <%=TradeConfig.getMAX_USERS()%>
                                                </B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="2"><A
                                                    href="docs/glossary.html">Active
                                                        Stocks / Trade
                                                        Stock population</A></TD>
                                                <TD colspan="3"><B><%=TradeConfig.getMAX_QUOTES()%>
                                                        / <%=runStatsData.getTradeStockCount()%></B></TD>
                                            </TR>
                                            <TR>
                                                <TD colspan="5"
                                                    align="center"
                                                    bgcolor="#f3f3f3"><B>Benchmark
                                                        scenario
                                                        verification</B></TD>
                                            </TR>
                                            <TR bgcolor="#fafcb6"
                                                align="center">
                                                <TD width="109"><B>Run
                                                        Statistic</B></TD>
                                                <TD><B>Scenario
                                                        verification
                                                        test</B></TD>
                                                <TD><B>Expected
                                                        Value</B></TD>
                                                <TD width="25"><B>Actual
                                                        Value</B></TD>
                                                <TD><B>Pass/Fail</B></TD>
                                            </TR>
                                            <TR>
                                                <TD>Active Stocks</TD>
                                                <TD>Active stocks
                                                    should generally
                                                    equal the db
                                                    population of stocks</TD>
                                                <TD><%=runStatsData.getTradeStockCount()%></TD>
                                                <TD><B><%=TradeConfig.getMAX_QUOTES()%></B></TD>
                                                <TD><%=(runStatsData.getTradeStockCount() == TradeConfig.getMAX_QUOTES()) ? "Pass" : "Warn"%></TD>
                                            </TR>
                                            <TR bgcolor="#ffffff">
                                                <TD><A
                                                    href="docs/glossary.html">Active
                                                        Traders</A></TD>
                                                <TD>Active traders
                                                    should generally
                                                    equal the db
                                                    population of
                                                    traders</TD>
                                                <TD><%=runStatsData.getTradeUserCount()%></TD>
                                                <TD><B><%=TradeConfig.getMAX_USERS()%></B></TD>
                                                <TD><%=(runStatsData.getTradeUserCount() == TradeConfig.getMAX_USERS()) ? "Pass" : "Warn"%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Estimated
                                                        total requests</A></TD>
                                                <TD>Actual
                                                    benchmark scenario
                                                    requests should be
                                                    within +/- 2% of the
                                                    estimated number of
                                                    requests in the last
                                                    benchmark run to
                                                    pass.</TD>
                                                <TD><%=expectedRequests%></TD>
                                                <TD><B>see</B><B><SUP>2</SUP></B></TD>
                                                <TD>see<SUP>2</SUP></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">New
                                                        Users Registered
                                                </A></TD>
                                                <TD><%=registerPercentage * 100%>%
                                                    of expected requests
                                                    (<%=registerPercentage%>
                                                    * <%=expectedRequests%>
                                                    )</TD>
                                                <TD><%=registerPercentage * expectedRequests%></TD>
                                                <TD><B><%=runStatsData.getNewUserCount()%></B></TD>
                                                <TD><%=verify(registerPercentage * expectedRequests, (double) runStatsData.getNewUserCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Logins
                                                </A></TD>
                                                <TD><%=loginPercentage * 100%>%
                                                    of expected requests
                                                    (<%=loginPercentage%>
                                                    * <%=expectedRequests%>
                                                    ) + initial login</TD>
                                                <TD><%=loginPercentage * expectedRequests + runStatsData.getTradeUserCount()%></TD>
                                                <TD><B><%=runStatsData.getSumLoginCount() + runStatsData.getTradeUserCount()%></B></TD>
                                                <TD><%=verify((double) loginPercentage * expectedRequests, (double) runStatsData.getSumLoginCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Logouts
                                                </A></TD>
                                                <TD>#logouts must
                                                    be &gt;=
                                                    #logins-active
                                                    traders ( <%=runStatsData.getSumLoginCount()%>
                                                    - <%=TradeConfig.getMAX_USERS()%>
                                                    )
                                                </TD>
                                                <TD><%=runStatsData.getSumLoginCount() - TradeConfig.getMAX_USERS()%></TD>
                                                <TD><B><%=runStatsData.getSumLogoutCount()%></B></TD>
                                                <TD><%=(runStatsData.getSumLogoutCount() >= (runStatsData.getSumLoginCount() - TradeConfig.getMAX_USERS())) ? "Pass" : "Fail<SUP>4</SUP>"%>
                                                </TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">User
                                                        Holdings </A></TD>
                                                <TD>Trade users own
                                                    an average of 5
                                                    holdings, 5* total
                                                    Users = ( 5 * <%=runStatsData.getTradeUserCount()%>)
                                                </TD>
                                                <TD><%=5 * runStatsData.getTradeUserCount()%></TD>
                                                <TD><B><%=runStatsData.getHoldingCount()%></B></TD>
                                                <TD><%=verify(5 * runStatsData.getTradeUserCount(), runStatsData.getHoldingCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Buy
                                                        Order Count </A></TD>
                                                <TD><%=buyOrderPercentage * 100%>%
                                                    of expected requests
                                                    (<%=buyOrderPercentage%>
                                                    * <%=expectedRequests%>
                                                    ) + current holdings
                                                    count</TD>
                                                <TD><%=buyOrderPercentage * expectedRequests + runStatsData.getHoldingCount()%></TD>
                                                <TD><B><%=runStatsData.getBuyOrderCount()%></B></TD>
                                                <TD><%=verify(buyOrderPercentage * expectedRequests + runStatsData.getHoldingCount(), (double) runStatsData.getBuyOrderCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Sell
                                                        Order Count </A></TD>
                                                <TD><%=sellOrderPercentage * 100%>%
                                                    of expected requests
                                                    (<%=sellOrderPercentage%>
                                                    * <%=expectedRequests%>
                                                    )</TD>
                                                <TD><%=sellOrderPercentage * expectedRequests%></TD>
                                                <TD><B><%=runStatsData.getSellOrderCount()%></B></TD>
                                                <TD><%=verify(sellOrderPercentage * expectedRequests, (double) runStatsData.getSellOrderCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Total
                                                        Order Count </A></TD>
                                                <TD><%=orderPercentage * 100%>%
                                                    of expected requests
                                                    (<%=orderPercentage%>
                                                    * <%=expectedRequests%>
                                                    ) + current holdings
                                                    count</TD>
                                                <TD><%=orderPercentage * expectedRequests + runStatsData.getHoldingCount()%></TD>
                                                <TD><B><%=runStatsData.getOrderCount()%></B></TD>
                                                <TD><%=verify(orderPercentage * expectedRequests + runStatsData.getHoldingCount(), (double) runStatsData.getOrderCount(), verifyPercent)%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Open
                                                        Orders </A></TD>
                                                <TD>All orders
                                                    should be completed
                                                    before reset<SUP>3</SUP>
                                                </TD>
                                                <TD>0</TD>
                                                <TD><B><%=runStatsData.getOpenOrderCount()%></B></TD>
                                                <TD><%=(runStatsData.getOpenOrderCount() > 0) ? "Fail<SUP>4</SUP>" : "Pass"%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Cancelled
                                                        Orders </A></TD>
                                                <TD>Orders are
                                                    cancelled if an
                                                    error is encountered
                                                    during order
                                                    processing.</TD>
                                                <TD>0</TD>
                                                <TD><B><%=runStatsData.getCancelledOrderCount()%></B></TD>
                                                <TD><%=(runStatsData.getCancelledOrderCount() > 0) ? "Fail<SUP>4</SUP>" : "Pass"%></TD>
                                            </TR>
                                            <TR>
                                                <TD><A
                                                    href="docs/glossary.html">Orders
                                                        remaining after
                                                        reset </A></TD>
                                                <TD>After Trade
                                                    reset, each user
                                                    should carry an
                                                    average of 5 orders
                                                    in the database. 5*
                                                    total Users = (5 * <%=runStatsData.getTradeUserCount()%>)
                                                </TD>
                                                <TD><%=5 * runStatsData.getTradeUserCount()%></TD>
                                                <TD><B><%=runStatsData.getOrderCount() - runStatsData.getDeletedOrderCount()%></B></TD>
                                                <TD><%=verify(5 * runStatsData.getTradeUserCount(), runStatsData.getOrderCount() - runStatsData.getDeletedOrderCount(), verifyPercent)%></TD>
                                            </TR>
                                        </TBODY>
                                    </TABLE>
                                    <CENTER>
                                        <DIV align="left"></DIV>
                                    </CENTER>
                                </TD>
                            </TR>
                            <TR>
                                <TD colspan="6">
                                    <OL>
                                        <LI><FONT size="-1">
                                                Benchmark verification
                                                tests require a Trade
                                                Reset between each
                                                benchmark run.</FONT></LI>
                                        <LI><FONT size="-1">The
                                                expected value of
                                                benchmark requests is
                                                computed based on the
                                                the count from the Web
                                                application since the
                                                last Trade reset.The
                                                actual value of
                                                benchmark request
                                                requires user
                                                verification and may be
                                                incorrect for a cluster.</FONT></LI>
                                        <LI><FONT size="-1">Orders
                                                are processed
                                                asynchronously in Trade.
                                                Therefore, processing
                                                may continue beyond the
                                                end of a benchmark run.
                                                Trade Reset should not
                                                be invoked until
                                                processing is completed.</FONT></LI>
                                        <LI><FONT size="-1">Actual
                                                values must be within</FONT><FONT
                                            size="-1" color="#cc0000"><FONT
                                                size="-1"
                                                color="#cc0000"><B>
                                                        <FONT size="-1"
                                                        color="#cc0000"><%=TradeConfig.verifyPercent%></FONT>%
                                                </B></FONT></FONT><FONT size="-1">of
                                                corresponding estimated
                                                values to pass
                                                verification.</FONT></LI>
                                    </OL>
                                </TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
        </TBODY>
    </TABLE>
    <FORM action="config" method="POST">
        <INPUT type="hidden" name="action" value="updateConfig">
        <TABLE height="54" style="font-size: smaller">
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
                        height="10" colspan=5><B><FONT
                            color="#ffffff">DayTrader Scenario
                                Runtime Statistics</FONT></B></TD>
                    <TD align="center" bgcolor="#000000" width="100"
                        height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
                </TR>
                <TR>
    
                </TR>
            </TBODY>
        </TABLE>
    </FORM>
</BODY>
</HTML>
