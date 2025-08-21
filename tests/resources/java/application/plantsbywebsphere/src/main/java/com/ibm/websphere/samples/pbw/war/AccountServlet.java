//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.war;

import java.io.IOException;

import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.ServletConfig;
import javax.servlet.ServletContext;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.pbw.bean.CatalogMgr;
import com.ibm.websphere.samples.pbw.bean.CustomerMgr;
import com.ibm.websphere.samples.pbw.jpa.Customer;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Servlet to handle customer account actions, such as login and register.
 */
@Named(value = "accountservlet")
@WebServlet("/servlet/AccountServlet")
public class AccountServlet extends HttpServlet {
	private static final long serialVersionUID = 1L;
	// Servlet action codes.
	public static final String ACTION_ACCOUNT = "account";
	public static final String ACTION_ACCOUNTUPDATE = "accountUpdate";
	public static final String ACTION_LOGIN = "login";
	public static final String ACTION_REGISTER = "register";
	public static final String ACTION_SETLOGGING = "SetLogging";

	@Inject
	private CustomerMgr login;
	@Inject
	private CatalogMgr catalog;

	/**
	 * Servlet initialization.
	 */
	public void init(ServletConfig config) throws ServletException {
		super.init(config);
	}

	/**
	 * Process incoming HTTP GET requests
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	public void doGet(javax.servlet.http.HttpServletRequest request,
			javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
		performTask(request, response);
	}

	/**
	 * Process incoming HTTP POST requests
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	public void doPost(javax.servlet.http.HttpServletRequest request,
			javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
		performTask(request, response);
	}

	/**
	 * Main service method for AccountServlet
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	private void performTask(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		String action = null;

		action = req.getParameter(Util.ATTR_ACTION);
		Util.debug("action=" + action);

		if (action.equals(ACTION_LOGIN)) {
			try {
				HttpSession session = req.getSession(true);
				String userid = req.getParameter("userid");
				String passwd = req.getParameter("passwd");
				String updating = req.getParameter(Util.ATTR_UPDATING);

				String results = null;
				if (Util.validateString(userid)) {
					results = login.verifyUserAndPassword(userid, passwd);
				} else {
					// user id was invalid, and may contain XSS attack
					results = "\nEmail address was invalid.";
					Util.debug("User id or email address was invalid. id=" + userid);
				}

				// If results have an error msg, return it, otherwise continue.
				if (results != null) {
					// Proliferate UPDATING flag if user is trying to update his account.
					if (updating.equals("true"))
						req.setAttribute(Util.ATTR_UPDATING, "true");

					req.setAttribute(Util.ATTR_RESULTS, results);
					requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_LOGIN);
				} else {
					// If not logging in for the first time, then clear out the
					// session data for the old user.
					if (session.getAttribute(Util.ATTR_CUSTOMER) != null) {
						session.removeAttribute(Util.ATTR_CART);
						// session.removeAttribute(Util.ATTR_CART_CONTENTS);
						session.removeAttribute(Util.ATTR_CHECKOUT);
						session.removeAttribute(Util.ATTR_ORDERKEY);
					}

					// Store customer userid in HttpSession.
					Customer customer = login.getCustomer(userid);
					session.setAttribute(Util.ATTR_CUSTOMER, customer);
					Util.debug("updating=" + updating + "=");

					// Was customer trying to edit account information.
					if (updating.equals("true")) {
						req.setAttribute(Util.ATTR_EDITACCOUNTINFO, customer);

						requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_ACCOUNT);
					} else {
						// See if user was in the middle of checking out.
						Boolean checkingOut = (Boolean) session.getAttribute(Util.ATTR_CHECKOUT);
						Util.debug("checkingOut=" + checkingOut + "=");
						if ((checkingOut != null) && (checkingOut.booleanValue())) {
							Util.debug("must be checking out");
							requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_ORDERINFO);
						} else {
							Util.debug("must NOT be checking out");
							String url;
							String category = (String) session.getAttribute(Util.ATTR_CATEGORY);

							// Default to plants
							if ((category == null) || (category.equals("null"))) {
								url = Util.PAGE_PROMO;
							} else {
								url = Util.PAGE_SHOPPING;
								req.setAttribute(Util.ATTR_INVITEMS, catalog
										.getItemsByCategory(Integer.parseInt(category)));
							}

							requestDispatch(getServletConfig().getServletContext(), req, resp, url);
						}
					}
				}
			} catch (ServletException e) {
				req.setAttribute(Util.ATTR_RESULTS, "/nException occurred");
				throw e;
			} catch (Exception e) {
				req.setAttribute(Util.ATTR_RESULTS, "/nException occurred");
				throw new ServletException(e.getMessage());
			}
		} else if (action.equals(ACTION_REGISTER)) {
			// Register a new user.
			// try
			// {
			String url;
			HttpSession session = req.getSession(true);

			String userid = req.getParameter("userid");
			String password = req.getParameter("passwd");
			String cpassword = req.getParameter("vpasswd");
			String firstName = req.getParameter("fname");
			String lastName = req.getParameter("lname");
			String addr1 = req.getParameter("addr1");
			String addr2 = req.getParameter("addr2");
			String addrCity = req.getParameter("city");
			String addrState = req.getParameter("state");
			String addrZip = req.getParameter("zip");
			String phone = req.getParameter("phone");

			// validate all user input
			if (!Util.validateString(userid)) {
				req.setAttribute(Util.ATTR_RESULTS, "Email address contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(firstName)) {
				req.setAttribute(Util.ATTR_RESULTS, "First Name contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(lastName)) {
				req.setAttribute(Util.ATTR_RESULTS, "Last Name contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(addr1)) {
				req.setAttribute(Util.ATTR_RESULTS, "Address Line 1 contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(addr2)) {
				req.setAttribute(Util.ATTR_RESULTS, "Address Line 2 contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(addrCity)) {
				req.setAttribute(Util.ATTR_RESULTS, "City contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(addrState)) {
				req.setAttribute(Util.ATTR_RESULTS, "State contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(addrZip)) {
				req.setAttribute(Util.ATTR_RESULTS, "Zip contains invalid characters.");
				url = Util.PAGE_REGISTER;
			} else if (!Util.validateString(phone)) {
				req.setAttribute(Util.ATTR_RESULTS, "Phone Number contains invalid characters.");
				url = Util.PAGE_REGISTER;
			}
			// Make sure passwords match.
			else if (!password.equals(cpassword)) {
				req.setAttribute(Util.ATTR_RESULTS, "Passwords do not match.");
				url = Util.PAGE_REGISTER;
			} else {
				// Create the new user.
				Customer customer = login
						.createCustomer(userid, password, firstName, lastName, addr1, addr2, addrCity, addrState, addrZip, phone);

				if (customer != null) {
					// Store customer info in HttpSession.
					session.setAttribute(Util.ATTR_CUSTOMER, customer);

					// See if user was in the middle of checking out.
					Boolean checkingOut = (Boolean) session.getAttribute(Util.ATTR_CHECKOUT);
					if ((checkingOut != null) && (checkingOut.booleanValue())) {
						url = Util.PAGE_ORDERINFO;
					} else {
						String category = (String) session.getAttribute(Util.ATTR_CATEGORY);

						// Default to plants
						if (category == null) {
							url = Util.PAGE_PROMO;
						} else {
							url = Util.PAGE_SHOPPING;
							req.setAttribute(Util.ATTR_INVITEMS, catalog
									.getItemsByCategory(Integer.parseInt(category)));
						}
					}
				} else {
					url = Util.PAGE_REGISTER;
					req.setAttribute(Util.ATTR_RESULTS, "New user NOT created!");
				}
			}
			requestDispatch(getServletConfig().getServletContext(), req, resp, url);
			// }
			// catch (CreateException e) { }
		} else if (action.equals(ACTION_ACCOUNT)) {
			String url;
			HttpSession session = req.getSession(true);
			Customer customer = (Customer) session.getAttribute(Util.ATTR_CUSTOMER);
			if (customer == null) {
				url = Util.PAGE_LOGIN;
				req.setAttribute(Util.ATTR_UPDATING, "true");
				req.setAttribute(Util.ATTR_RESULTS, "\nYou must login first.");
			} else {
				url = Util.PAGE_ACCOUNT;
				req.setAttribute(Util.ATTR_EDITACCOUNTINFO, customer);
			}
			requestDispatch(getServletConfig().getServletContext(), req, resp, url);
		} else if (action.equals(ACTION_ACCOUNTUPDATE)) {
			// try
			// {
			String url;
			HttpSession session = req.getSession(true);
			Customer customer = (Customer) session.getAttribute(Util.ATTR_CUSTOMER);

			String userid = customer.getCustomerID();
			String firstName = req.getParameter("fname");
			String lastName = req.getParameter("lname");
			String addr1 = req.getParameter("addr1");
			String addr2 = req.getParameter("addr2");
			String addrCity = req.getParameter("city");
			String addrState = req.getParameter("state");
			String addrZip = req.getParameter("zip");
			String phone = req.getParameter("phone");

			// Create the new user.
			customer = login.updateUser(userid, firstName, lastName, addr1, addr2, addrCity, addrState, addrZip, phone);
			// Store updated customer info in HttpSession.
			session.setAttribute(Util.ATTR_CUSTOMER, customer);

			// See if user was in the middle of checking out.
			Boolean checkingOut = (Boolean) session.getAttribute(Util.ATTR_CHECKOUT);
			if ((checkingOut != null) && (checkingOut.booleanValue())) {
				url = Util.PAGE_ORDERINFO;
			} else {
				String category = (String) session.getAttribute(Util.ATTR_CATEGORY);

				// Default to plants
				if (category == null) {
					url = Util.PAGE_PROMO;
				} else {
					url = Util.PAGE_SHOPPING;
					req.setAttribute(Util.ATTR_INVITEMS, catalog.getItemsByCategory(Integer.parseInt(category)));
				}
			}

			requestDispatch(getServletConfig().getServletContext(), req, resp, url);
			// }
			// catch (CreateException e) { }
		} else if (action.equals(ACTION_SETLOGGING)) {
			String debugSetting = req.getParameter("logging");
			if ((debugSetting == null) || (!debugSetting.equals("debug")))
				Util.setDebug(false);
			else
				Util.setDebug(true);

			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_HELP);
		}
	}

	/**
	 * Request dispatch.
	 */
	private void requestDispatch(ServletContext ctx,
			HttpServletRequest req,
			HttpServletResponse resp,
			String page) throws ServletException, IOException {
		resp.setContentType("text/html");
		ctx.getRequestDispatcher(page).include(req, resp);
	}
}