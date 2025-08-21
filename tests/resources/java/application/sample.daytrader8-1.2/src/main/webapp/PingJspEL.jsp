<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN">
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
<html>
<head>
<title>PingJspEL</title>
</head>
<body>
    <%@ page
        import="com.ibm.websphere.samples.daytrader.util.TradeConfig,com.ibm.websphere.samples.daytrader.entities.QuoteDataBean"
        session="false"%>

    <%!int hitCount = 0;
    String initTime = new java.util.Date().toString();%>

    <%
        // setup some variables to work with later
        int someint1 = TradeConfig.rndInt(100) + 1;
        pageContext.setAttribute("someint1", new Integer(someint1));
        int someint2 = TradeConfig.rndInt(100) + 1;
        pageContext.setAttribute("someint2", new Integer(someint2));
        float somefloat1 = TradeConfig.rndFloat(100) + 1.0f;
        pageContext.setAttribute("somefloat1", new Float(somefloat1));
        float somefloat2 = TradeConfig.rndFloat(100) + 1.0f;
        pageContext.setAttribute("somefloat2", new Float(somefloat2));

        QuoteDataBean quoteData1 = QuoteDataBean.getRandomInstance();
        pageContext.setAttribute("quoteData1", quoteData1);
        QuoteDataBean quoteData2 = QuoteDataBean.getRandomInstance();
        pageContext.setAttribute("quoteData2", quoteData2);
        QuoteDataBean quoteData3 = QuoteDataBean.getRandomInstance();
        pageContext.setAttribute("quoteData3", quoteData3);
        QuoteDataBean quoteData4 = QuoteDataBean.getRandomInstance();
        pageContext.setAttribute("quoteData4", quoteData4);

        QuoteDataBean quoteData[] = new QuoteDataBean[4];
        quoteData[0] = quoteData1;
        quoteData[1] = quoteData2;
        quoteData[2] = quoteData3;
        quoteData[3] = quoteData4;
        pageContext.setAttribute("quoteData", quoteData);
    %>

    <HR>
    <BR>
    <FONT size="+2" color="#000066">PING JSP EL:<BR></FONT>
    <FONT size="+1" color="#000066">Init time: <%=initTime%></FONT>
    <P>
        <B>Hit Count: <%=hitCount++%></B>
    </P>
    <HR>

    <P>

        someint1 =
        <%=someint1%><br /> someint2 =
        <%=someint2%><br /> somefloat1 =
        <%=somefloat1%><br /> somefloat2 =
        <%=somefloat2%><br />
    <P>
    <HR>

    <table border="1">
        <thead>
            <tr>
                <th>EL Type</th>
                <th>EL Expressions</th>
                <th>Result</th>
            <tr>
        </thead>
        <tr>
            <td>Integer Arithmetic</td>
            <td>\${someint1 + someint2 - someint1 * someint2 mod
                someint1}</td>
            <td>${someint1 + someint2 - someint1 * someint2 mod someint1}</td>
        </tr>
        <tr>
            <td>Floating Point Arithmetic</td>
            <td>\${somefloat1 + somefloat2 - somefloat1 *
                somefloat2 / somefloat1}</td>
            <td>${somefloat1 + somefloat2 - somefloat1 * somefloat2 / somefloat1}</td>
        </tr>
        <tr>
            <td>Logical Operations</td>
            <td>\${(someint1 < someint2) && (someint1 <= someint2)
                || (someint1 == someint2) && !Boolean.FALSE}</td>
            <td>${(someint1 < someint2) && (someint1 <= someint2) || (someint1 == someint2) && !Boolean.FALSE}</td>
        </tr>
        <tr>
            <td>Indexing Operations</td>
            <td>\${quoteData3.symbol}<br />
                \${quoteData[2].symbol}<br /> \${quoteData4["symbol"]}<br />
                \${header["host"]}<br /> \${header.host}<br />
            </td>
            <td>${quoteData3.symbol}<br /> ${quoteData[1].symbol}<br />
                ${quoteData4["symbol"]}<br /> ${header["host"]}<br />
                ${header.host}
            </td>
        </tr>
        <tr>
            <td>Variable Scope Tests</td>
            <td>\${(quoteData3 == null) ? "null" : quoteData3}<br />
                \${(noSuchVariableAtAnyScope == null) ? "null" : noSuchVariableAtAnyScope}
            </td>
            <td>${(quoteData3 == null) ? "null" : quoteData3}<br />
                ${(noSuchVariableAtAnyScope == null) ? "null" : noSuchVariableAtAnyScope}
            </td>
        </tr>
    </table>
</body>
</html>
